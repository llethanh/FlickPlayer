"""Tests for :class:`ExportEngine` (v0.5.0)."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from img_player.annotate.store import AnnotationStore
from img_player.export.engine import ExportEngine
from img_player.export.settings import ExportSettings
from img_player.layers import Layer
from img_player.media.video_probe import probe_video
from img_player.sequence.scanner import scan


def _make_test_video(path: Path, n_frames: int = 8) -> None:
    """Encode a tiny all-keyframe h264 mp4 (no audio) so any frame is
    seekable. Each frame is a flat grey ramp so a decode failure (vs
    the OIIO-can't-read-mov crash) is distinguishable."""
    container = av.open(str(path), mode="w")
    vstream = container.add_stream("h264", rate=24)
    vstream.width = 64
    vstream.height = 48
    vstream.pix_fmt = "yuv420p"
    vstream.options = {"g": "1"}
    for i in range(n_frames):
        arr = np.full((48, 64, 3), (i * 20) % 256, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = i
        for packet in vstream.encode(frame):
            container.mux(packet)
    for packet in vstream.encode(None):
        container.mux(packet)
    container.close()


@pytest.fixture(scope="session")
def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sequence_info(sequence_dir: Path):
    return scan(sequence_dir / "render.0001.png", probe=False)


@pytest.fixture
def store(_qapp: QApplication) -> AnnotationStore:
    return AnnotationStore()


# ============================================================================
# Successful end-to-end: image sequence
# ============================================================================


class TestEndToEndImageSeq:
    def test_writes_complete_sequence(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path,
            in_frame=1, out_frame=5,
            format_key="png",
            start_frame=1,
            apply_display_transform=False,
            bake_annotations=False,
        )
        engine = ExportEngine(
            settings=settings,
            sequence=sequence_info,
            annotation_store=store,
            ocio_manager=None,
            source_colorspace=None,
            display=None,
            view=None,
        )
        result = engine.run()
        assert result.canceled is False
        assert result.frames_written == 5
        files = sorted(tmp_path.glob("render.*.png"))
        assert len(files) == 5

    def test_progress_callback_fires_per_frame(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=4, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
        )
        progress_log: list[tuple[int, int]] = []
        engine.run(progress_cb=lambda c, t, _f: progress_log.append((c, t)))
        # Expect monotonically increasing currents 1..4 with total=4 each.
        assert progress_log == [(1, 4), (2, 4), (3, 4), (4, 4)]


# ============================================================================
# Cancellation
# ============================================================================


class TestEndToEndVideo:
    """The 2026-06-18 bug: exporting a video LAYER (.mov / .mp4) blew
    up because the renderer fed the container path to OpenImageIO,
    which can't read video. The renderer now decodes video sources
    through PyAV."""

    def test_exports_video_layer_to_png_sequence(
        self, store, tmp_path: Path, _qapp,
    ) -> None:
        vid = tmp_path / "clip.mp4"
        _make_test_video(vid, n_frames=8)
        layer = Layer.from_video(probe_video(vid))
        seq = layer.sequence  # synthetic: frames 0..7 → clip.mp4
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        settings = ExportSettings(
            output_dir=out_dir,
            in_frame=seq.first_frame, out_frame=seq.last_frame,
            format_key="png",
            start_frame=0,
            apply_display_transform=False,
            bake_annotations=False,
        )
        engine = ExportEngine(
            settings=settings,
            sequence=seq,
            annotation_store=store,
            ocio_manager=None,
            source_colorspace=None,
            display=None,
            view=None,
        )
        result = engine.run()
        assert result.canceled is False
        assert result.frames_written == 8
        files = sorted(out_dir.glob("*.png"))
        assert len(files) == 8
        # Sanity: the first PNG is a real image, not a 1×1 black stub.
        import OpenImageIO as oiio
        inp = oiio.ImageInput.open(str(files[0]))
        assert inp is not None
        spec = inp.spec()
        inp.close()
        assert (spec.width, spec.height) == (64, 48)


class TestCancel:
    def test_cancel_stops_loop_and_keeps_partial_output(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        """Cancel returns ``canceled=True`` and leaves the partial
        files on disk — the engine no longer auto-deletes (the
        orchestrator prompts the user via ``_handle_cancel_cleanup``
        and calls ``discard_partial_output`` only when they pick
        "Supprimer")."""
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
        )
        def _cb(current: int, _t: int, _f: float) -> None:
            if current == 2:
                engine.cancel()
        result = engine.run(progress_cb=_cb)
        assert result.canceled is True
        assert result.frames_written == 2
        # Files stay on disk by default — engine closed the writer
        # cleanly without deleting.
        files = list(tmp_path.glob("*.png"))
        assert len(files) == 2

    def test_discard_partial_output_deletes_files(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        """``discard_partial_output`` is the explicit opt-in the
        orchestrator calls after the user confirms deletion in the
        cancel dialog. Should remove every frame the writer wrote
        before cancellation."""
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
        )
        def _cb(current: int, _t: int, _f: float) -> None:
            if current == 2:
                engine.cancel()
        engine.run(progress_cb=_cb)
        assert len(list(tmp_path.glob("*.png"))) == 2
        engine.discard_partial_output()
        assert list(tmp_path.glob("*.png")) == []


# ============================================================================
# Error path
# ============================================================================


class TestErrorPath:
    def test_invalid_settings_rejected_early(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path,
            in_frame=10, out_frame=1,  # inverted!
            format_key="png",
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
        )
        with pytest.raises(Exception):
            engine.run()
        # No writer should have been created → no files.
        files = list(tmp_path.glob("*.png"))
        assert files == []


# ============================================================================
# Sidecar copy
# ============================================================================


class TestSidecarCopy:
    def test_sidecar_copied_when_enabled(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        sidecar = tmp_path / "fake_sidecar.json"
        sidecar.write_text("{\"frames\": {}}")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        settings = ExportSettings(
            output_dir=out_dir,
            in_frame=1, out_frame=2,
            format_key="png",
            apply_display_transform=False,
            bake_annotations=True,
            copy_sidecar=True,
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
            sidecar_source=sidecar,
        )
        engine.run()
        assert (out_dir / sidecar.name).exists()

    def test_sidecar_skipped_when_disabled(
        self, sequence_info, store, tmp_path: Path, _qapp,
    ) -> None:
        sidecar = tmp_path / "fake_sidecar.json"
        sidecar.write_text("{}")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        settings = ExportSettings(
            output_dir=out_dir,
            in_frame=1, out_frame=1,
            format_key="png",
            apply_display_transform=False,
            bake_annotations=True,
            copy_sidecar=False,
        )
        engine = ExportEngine(
            settings=settings, sequence=sequence_info, annotation_store=store,
            ocio_manager=None,
            source_colorspace=None, display=None, view=None,
            sidecar_source=sidecar,
        )
        engine.run()
        assert not (out_dir / sidecar.name).exists()
