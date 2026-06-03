"""Tests for ``img_player.media.video_probe``.

Generates a tiny mp4 (and a video+audio mp4) on the fly with PyAV so
the suite has no fixture files to ship — keeps the test data small,
deterministic, and re-creatable across machines.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from img_player.media.video_probe import (
    VIDEO_EXTENSIONS,
    is_video_file,
    probe_video,
)

av = pytest.importorskip("av")


def _make_video(path: Path, *, n_frames: int = 12, fps: int = 24,
                width: int = 64, height: int = 48,
                with_audio: bool = False) -> None:
    """Encode a short H.264 mp4 (and optional AAC stereo) to ``path``.

    Tiny dimensions keep encode time under ~50 ms; libx264 + faststart
    is what 90% of mp4s in the wild look like, so probing this matches
    realistic input.
    """
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("h264", rate=fps)
    vstream.width = width
    vstream.height = height
    vstream.pix_fmt = "yuv420p"

    astream = None
    sample_rate = 48000
    if with_audio:
        astream = container.add_stream("aac", rate=sample_rate)
        astream.layout = "stereo"

    # Solid-grey frames — content is irrelevant, we only need a valid
    # bitstream the probe can read.
    arr = np.full((height, width, 3), 128, dtype=np.uint8)
    for i in range(n_frames):
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = i
        for packet in vstream.encode(frame):
            container.mux(packet)

    if astream is not None:
        # 0.5s of silence at 48 kHz stereo, packed in fltp planes
        # (PyAV's required layout for AAC). Two planes of zeros.
        n_samples_total = sample_rate // 2
        chunk = 1024
        pts = 0
        for start in range(0, n_samples_total, chunk):
            length = min(chunk, n_samples_total - start)
            samples = np.zeros((2, length), dtype=np.float32)
            aframe = av.AudioFrame.from_ndarray(samples, format="fltp",
                                                layout="stereo")
            aframe.sample_rate = sample_rate
            aframe.pts = pts
            pts += length
            for packet in astream.encode(aframe):
                container.mux(packet)
        for packet in astream.encode(None):
            container.mux(packet)

    for packet in vstream.encode(None):
        container.mux(packet)
    container.close()


def test_extension_set_includes_common_containers() -> None:
    assert ".mp4" in VIDEO_EXTENSIONS
    assert ".mov" in VIDEO_EXTENSIONS
    assert ".exr" not in VIDEO_EXTENSIONS


def test_webm_is_accepted_as_video() -> None:
    # v1.8.1 opt-in: .webm (VP8/VP9/AV1 + Opus/Vorbis) goes through
    # the same PyAV path as .mp4. All decoders are bundled (libvpx,
    # aom, opus, vorbis, ogg), so the router only needed extension
    # recognition. Pin both the membership in VIDEO_EXTENSIONS and
    # the is_video_file helper so a refactor can't silently drop
    # .webm by re-narrowing the set.
    assert ".webm" in VIDEO_EXTENSIONS
    assert is_video_file("foo.webm")
    assert is_video_file(Path("/tmp/clip.WEBM"))  # case-insensitive


def test_is_video_file_extension_match() -> None:
    assert is_video_file("foo.mp4")
    assert is_video_file(Path("/tmp/clip.MOV"))  # case-insensitive
    assert not is_video_file("foo.exr")
    assert not is_video_file("foo.png")


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe_video(tmp_path / "nope.mp4")


def test_probe_video_only_mp4(tmp_path: Path) -> None:
    p = tmp_path / "tiny.mp4"
    _make_video(p, n_frames=12, fps=24)
    meta = probe_video(p)

    assert meta.path == p
    assert meta.has_video is True
    assert meta.has_audio is False
    assert meta.width == 64
    assert meta.height == 48
    assert meta.fps == Fraction(24, 1)
    assert meta.video_codec == "h264"
    assert meta.pixel_format == "yuv420p"
    # H.264 in mp4 may report nb_frames or only duration — both paths
    # in the probe should land on a sensible count.
    assert meta.frame_count is not None
    assert meta.frame_count >= 10


def test_probe_video_with_audio(tmp_path: Path) -> None:
    p = tmp_path / "av.mp4"
    _make_video(p, n_frames=12, fps=24, with_audio=True)
    meta = probe_video(p)

    assert meta.has_video is True
    assert meta.has_audio is True
    assert meta.audio_codec == "aac"
    assert meta.audio_sample_rate == 48000
    assert meta.audio_channels == 2
