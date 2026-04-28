"""Tests for :class:`FrameRenderer` (v0.5.0)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from img_player.annotate.store import AnnotationStore
from img_player.annotate.stroke import Stroke
from img_player.export.renderer import FrameRenderer, RenderContext
from img_player.export.settings import ExportSettings
from img_player.sequence.scanner import scan


@pytest.fixture(scope="session")
def _qapp() -> QApplication:
    """QPainter on a QImage requires a QApplication instance — just
    instantiate one for the test process if not already alive."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def sequence_info(sequence_dir: Path):
    """Use the conftest 10-frame PNG sequence."""
    return scan(sequence_dir / "render.0001.png", probe=False)


@pytest.fixture
def annotation_store(_qapp: QApplication) -> AnnotationStore:
    store = AnnotationStore()
    # Frame 1 has a stroke, frames 2+ are clean.
    store.add_stroke(
        1,
        Stroke(points=((2.0, 2.0), (10.0, 10.0)), color="#E84A4A", size=2.0),
    )
    return store


# ============================================================================
# Basic render path
# ============================================================================


class TestBasicRender:
    def test_render_returns_uint8_for_png(
        self, sequence_info, annotation_store, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            apply_display_transform=False,  # OCIO disabled
            bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (16, 16))
        assert out.dtype == np.uint8
        assert out.shape[0] == 16 and out.shape[1] == 16

    def test_render_returns_uint16_for_tiff(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="tiff",
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (16, 16))
        assert out.dtype == np.uint16

    def test_render_returns_float16_for_exr(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="exr",
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (16, 16))
        assert out.dtype == np.float16

    def test_render_returns_uint8_for_video(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="h264_mp4",
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (16, 16))
        assert out.dtype == np.uint8

    def test_missing_frame_raises(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        with pytest.raises(FileNotFoundError):
            r.render(99, (16, 16))


# ============================================================================
# Resize
# ============================================================================


class TestResize:
    def test_resize_to_smaller(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            width=8, height=8,
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (8, 8))
        assert out.shape[:2] == (8, 8)

    def test_resize_to_larger(
        self, sequence_info, tmp_path: Path, _qapp,
    ) -> None:
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            width=64, height=64,
            apply_display_transform=False, bake_annotations=False,
        )
        ctx = RenderContext(sequence=sequence_info, annotation_store=None)
        r = FrameRenderer(ctx, settings)
        out = r.render(1, (64, 64))
        assert out.shape[:2] == (64, 64)


# ============================================================================
# Annotation bake
# ============================================================================


class TestAnnotationBake:
    def test_bake_off_pass_through(
        self, sequence_info, annotation_store, tmp_path: Path, _qapp,
    ) -> None:
        # bake_annotations=False — output should be byte-equal to no-store render.
        settings_off = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        ctx_no_store = RenderContext(sequence=sequence_info, annotation_store=None)
        ctx_with_store = RenderContext(
            sequence=sequence_info, annotation_store=annotation_store,
        )
        r1 = FrameRenderer(ctx_no_store, settings_off)
        r2 = FrameRenderer(ctx_with_store, settings_off)
        assert np.array_equal(r1.render(1, (16, 16)), r2.render(1, (16, 16)))

    def test_bake_on_modifies_pixels(
        self, sequence_info, annotation_store, tmp_path: Path, _qapp,
    ) -> None:
        settings_no_bake = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            apply_display_transform=False, bake_annotations=False,
        )
        settings_bake = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png",
            apply_display_transform=False, bake_annotations=True,
        )
        ctx_no = RenderContext(sequence=sequence_info, annotation_store=None)
        ctx_yes = RenderContext(sequence=sequence_info, annotation_store=annotation_store)
        a = FrameRenderer(ctx_no, settings_no_bake).render(1, (16, 16))
        b = FrameRenderer(ctx_yes, settings_bake).render(1, (16, 16))
        # Stroke goes through the image — at least one pixel must differ.
        assert not np.array_equal(a, b)

    def test_bake_on_clean_frame_unchanged(
        self, sequence_info, annotation_store, tmp_path: Path, _qapp,
    ) -> None:
        # Frame 5 has no strokes — bake should be a no-op visually.
        settings = ExportSettings(
            output_dir=tmp_path, in_frame=5, out_frame=5, format_key="png",
            apply_display_transform=False, bake_annotations=True,
        )
        ctx_yes = RenderContext(sequence=sequence_info, annotation_store=annotation_store)
        ctx_no = RenderContext(sequence=sequence_info, annotation_store=None)
        a = FrameRenderer(ctx_no, settings.with_changes(bake_annotations=False)).render(
            5, (16, 16),
        )
        b = FrameRenderer(ctx_yes, settings).render(5, (16, 16))
        assert np.array_equal(a, b)
