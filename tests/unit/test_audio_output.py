"""Tests for ``img_player.media.audio_output.AudioOutput``.

Exercises the callback + control surface WITHOUT opening a real
sounddevice device — the audio device might be missing in CI. We
drive ``_callback`` directly and inspect the ring-buffer state.
Real-device sanity-checking is part of the manual smoke pass.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from img_player.media.audio_output import AudioOutput  # noqa: E402
from img_player.media.audio_source import AudioSource  # noqa: E402


def _make_av_file(path: Path, duration_s: float = 1.0) -> None:
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("h264", rate=24)
    vstream.width = 64
    vstream.height = 48
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"g": "1"}
    astream = container.add_stream("aac", rate=48000)
    astream.layout = "stereo"
    n_video = max(1, int(duration_s * 24))
    arr = np.full((48, 64, 3), 128, dtype=np.uint8)
    for i in range(n_video):
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = i
        for packet in vstream.encode(frame):
            container.mux(packet)
    n_samples = int(duration_s * 48000)
    chunk = 1024
    pts = 0
    for start in range(0, n_samples, chunk):
        length = min(chunk, n_samples - start)
        # Identifiable signal: full-scale square wave so the callback
        # output has a recognisable non-zero shape.
        block = np.ones((2, length), dtype=np.float32) * 0.5
        aframe = av.AudioFrame.from_ndarray(block, format="fltp", layout="stereo")
        aframe.sample_rate = 48000
        aframe.pts = pts
        pts += length
        for packet in astream.encode(aframe):
            container.mux(packet)
    for packet in astream.encode(None):
        container.mux(packet)
    for packet in vstream.encode(None):
        container.mux(packet)
    container.close()


def _drive_callback(out: AudioOutput, frames: int = 1024) -> np.ndarray:
    """Manually invoke ``_callback`` once, return the ``outdata`` buffer."""
    buf = np.zeros((frames, out._channels), dtype=np.float32)
    out._callback(buf, frames, None, None)
    return buf


def test_paused_outputs_silence(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    _make_av_file(p)
    out = AudioOutput()
    try:
        src = AudioSource(p)
        out.set_source("layer-1", src)
        # Default: not playing → silence even with a source set.
        buf = _drive_callback(out)
        assert np.allclose(buf, 0.0)
    finally:
        out.close()


def test_mute_flag_silences_output(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    _make_av_file(p)
    out = AudioOutput()
    try:
        src = AudioSource(p)
        out.set_source("layer-1", src)
        out.play()
        # Pre-load a chunk into the ring so the callback HAS data —
        # we want to verify the mute path overrides even when data
        # is available.
        out._ring.put(np.ones((1024, 2), dtype=np.float32) * 0.5)
        out.set_speed(2.0)  # mute (speed != 1.0)
        buf = _drive_callback(out)
        assert np.allclose(buf, 0.0)
    finally:
        out.close()


def test_speed_back_to_unity_unmutes(tmp_path: Path) -> None:
    out = AudioOutput()
    out.set_speed(2.0)
    assert out._mute is True
    out.set_speed(1.0)
    assert out._mute is False
    out.close()


def test_callback_drains_ring_buffer() -> None:
    """Pre-load ring with a known signal, drive callback, check shape."""
    out = AudioOutput()
    out.play()
    # Push two chunks so the callback has to drain across calls.
    out._ring.put(np.full((512, 2), 0.25, dtype=np.float32))
    out._ring.put(np.full((512, 2), 0.75, dtype=np.float32))
    buf = _drive_callback(out, frames=1024)
    # First half should be 0.25, second half 0.75.
    assert np.allclose(buf[:512], 0.25)
    assert np.allclose(buf[512:], 0.75)
    out.close()


def test_callback_underrun_fills_silence() -> None:
    out = AudioOutput()
    out.play()
    out._ring.put(np.full((256, 2), 0.5, dtype=np.float32))
    buf = _drive_callback(out, frames=1024)
    # First 256 samples = ring content; the remaining 768 = silence
    # (underrun, ring was empty).
    assert np.allclose(buf[:256], 0.5)
    assert np.allclose(buf[256:], 0.0)
    out.close()


def test_gain_scales_output() -> None:
    out = AudioOutput()
    out.play()
    out.set_gain(0.5)
    out._ring.put(np.ones((1024, 2), dtype=np.float32))
    buf = _drive_callback(out, frames=1024)
    assert np.allclose(buf, 0.5)
    out.close()


def test_set_source_swaps_and_flushes(tmp_path: Path) -> None:
    p = tmp_path / "a.mp4"
    _make_av_file(p)
    out = AudioOutput()
    try:
        src1 = AudioSource(p)
        out.set_source("layer-1", src1)
        # Pre-fill the ring with a marker chunk; switching source
        # should flush.
        out._ring.put(np.ones((512, 2), dtype=np.float32))
        src2 = AudioSource(p)
        out.set_source("layer-2", src2)
        assert out._ring.empty()
    finally:
        out.close()


def test_set_source_to_none_silences() -> None:
    out = AudioOutput()
    out.play()
    out.set_source(None, None)
    buf = _drive_callback(out)
    assert np.allclose(buf, 0.0)
    out.close()
