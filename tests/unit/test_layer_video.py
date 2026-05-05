"""Tests for ``Layer.from_video`` — the video-backed layer constructor.

Reuses the small mp4 generator from ``test_video_source`` so the
suite stays fixture-free.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from img_player.layers.models import Layer  # noqa: E402
from img_player.media.video_probe import probe_video  # noqa: E402


def _make_video(path: Path, *, n_frames: int = 24, fps: int = 24,
                width: int = 64, height: int = 48) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream("h264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"g": "1"}
    arr = np.full((height, width, 3), 128, dtype=np.uint8)
    for i in range(n_frames):
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = i
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()


def test_from_video_builds_synthetic_sequence(tmp_path: Path) -> None:
    p = tmp_path / "clip.mp4"
    _make_video(p, n_frames=24, fps=24)
    meta = probe_video(p)

    layer = Layer.from_video(meta)
    assert layer.is_video
    assert layer.video_metadata is meta
    # Synthetic sequence covers the full video frame range.
    assert layer.sequence.frame_count == 24
    assert layer.layer_in == 0
    assert layer.layer_out == 23
    assert layer.master_start == 0
    assert layer.master_end == 23
    # Width/height/fps propagated from probe metadata.
    assert layer.sequence.width == 64
    assert layer.sequence.height == 48
    assert layer.sequence.fps_default == 24.0
    # Default per-layer audio policy: not solo, not muted, unity gain.
    assert layer.audio_solo is False
    assert layer.audio_mute is False
    assert layer.audio_gain == 1.0


def test_from_video_with_offset(tmp_path: Path) -> None:
    p = tmp_path / "clip.mp4"
    _make_video(p, n_frames=12, fps=24)
    meta = probe_video(p)
    layer = Layer.from_video(meta, offset=100)
    # ``master_start`` follows offset; ``master_end`` follows offset + length - 1.
    assert layer.master_start == 100
    assert layer.master_end == 111
    assert layer.covers(105)
    assert not layer.covers(99)
    assert not layer.covers(112)


def test_from_video_source_frame_translation(tmp_path: Path) -> None:
    """``source_frame_at`` should map master frame → video frame index."""
    p = tmp_path / "clip.mp4"
    _make_video(p, n_frames=10, fps=24)
    meta = probe_video(p)
    layer = Layer.from_video(meta, offset=50)
    # Master frame 50 → video frame 0; master 55 → video 5.
    assert layer.source_frame_at(50) == 0
    assert layer.source_frame_at(55) == 5
    assert layer.source_frame_at(59) == 9


def test_from_video_rejects_audio_only(tmp_path: Path) -> None:
    """Layer.from_video on an audio-only file must raise."""
    p = tmp_path / "audio.m4a"
    container = av.open(str(p), mode="w")
    astream = container.add_stream("aac", rate=48000)
    astream.layout = "stereo"
    samples = np.zeros((2, 1024), dtype=np.float32)
    aframe = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
    aframe.sample_rate = 48000
    aframe.pts = 0
    for packet in astream.encode(aframe):
        container.mux(packet)
    for packet in astream.encode(None):
        container.mux(packet)
    container.close()
    meta = probe_video(p)
    assert not meta.has_video
    with pytest.raises(ValueError, match="non-video"):
        Layer.from_video(meta)


def test_image_sequence_layer_is_not_video(tmp_path: Path) -> None:
    """Sanity: ordinary sequence-backed layers report is_video=False."""
    from img_player.sequence.models import FrameInfo, SequenceInfo
    seq = SequenceInfo(
        base_name="img.",
        extension=".exr",
        directory=tmp_path,
        padding=4,
        frames=(FrameInfo(path=tmp_path / "img.0001.exr", frame_number=1),),
    )
    layer = Layer.from_sequence(seq)
    assert layer.is_video is False
    assert layer.video_metadata is None
    assert layer.audio_solo is False
    assert layer.audio_mute is False
