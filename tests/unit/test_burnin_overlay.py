"""Tests for the BurninOverlay widget — the Qt layer that paints the
active burnin over the GL viewport.

Headless via pytest-qt's ``qtbot``. We pin:

* Construction defaults (hidden, transparent to mouse).
* The cached pixmap rebuilds when the signature changes and reuses
  when it doesn't (the memo is the perf win for playback).
* ``set_enabled`` / ``set_template`` / ``set_context`` produce the
  expected visibility transitions.
* Resize invalidates the cache.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSize, Qt

from img_player.burnins.builtins import builtin_template
from img_player.burnins.model import BurninBar, BurninTemplate
from img_player.burnins.tokens import RenderContext
from img_player.ui.burnin_overlay import BurninOverlay


@pytest.fixture
def overlay(qtbot) -> BurninOverlay:  # type: ignore[no-untyped-def]
    w = BurninOverlay()
    qtbot.addWidget(w)
    w.resize(800, 450)
    return w


class TestConstruction:
    def test_hidden_by_default(self, overlay: BurninOverlay) -> None:
        # The View menu / Ctrl+B has to opt in — an empty player at
        # boot doesn't paint anything yet.
        assert not overlay.isVisible()

    def test_is_transparent_to_mouse_events(
        self, overlay: BurninOverlay,
    ) -> None:
        # Clicks fall through to the GL widget below.
        assert overlay.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
        )

    def test_is_translucent(self, overlay: BurninOverlay) -> None:
        # Translucent background so the GL widget shows through where
        # the burnin doesn't draw.
        assert overlay.testAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
        )

    def test_default_enabled_is_false(self, overlay: BurninOverlay) -> None:
        assert overlay.is_enabled() is False

    def test_default_template_is_none(self, overlay: BurninOverlay) -> None:
        assert overlay.template() is None


class TestVisibilityTransitions:
    def test_enabling_without_template_stays_hidden(
        self, overlay: BurninOverlay,
    ) -> None:
        # ``set_enabled(True)`` with no template should leave the
        # widget hidden — there's nothing to draw.
        overlay.set_enabled(True)
        assert not overlay.isVisible()

    def test_enabling_with_template_shows(self, overlay: BurninOverlay) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_enabled(True)
        assert overlay.isVisible()

    def test_disabling_hides(self, overlay: BurninOverlay) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_enabled(True)
        overlay.set_enabled(False)
        assert not overlay.isVisible()

    def test_clearing_template_while_enabled_hides(
        self, overlay: BurninOverlay,
    ) -> None:
        # The user can delete a template; the overlay should gracefully
        # disappear rather than paint a stale pixmap.
        overlay.set_template(builtin_template("default"))
        overlay.set_enabled(True)
        overlay.set_template(None)
        assert not overlay.isVisible()

    def test_enabled_state_survives_template_change(
        self, overlay: BurninOverlay,
    ) -> None:
        # Swapping templates while on stays on. We swap to a freshly
        # constructed instance of the same builtin — different object
        # identity is enough to exercise the swap path.
        overlay.set_template(builtin_template("default"))
        overlay.set_enabled(True)
        overlay.set_template(builtin_template("default"))
        assert overlay.is_enabled() is True
        assert overlay.isVisible()


class TestPixmapCaching:
    def test_first_paint_builds_pixmap(self, overlay: BurninOverlay) -> None:
        # No public access to the cached pixmap, so we check the
        # signature instead: it's None before any rebuild, set after.
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        # Force the paint pipeline by calling our private builder
        # directly (we don't render the actual paint event in a
        # headless test — that needs a backing store).
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
        assert overlay._cached_signature is not None

    def test_same_context_reuses_cached_pixmap(
        self, overlay: BurninOverlay,
    ) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        first = overlay._pixmap_for_current_state()
        second = overlay._pixmap_for_current_state()
        # Identical pixmap object — the renderer wasn't called twice.
        assert first is second

    def test_context_change_rebuilds_pixmap(
        self, overlay: BurninOverlay,
    ) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        first = overlay._pixmap_for_current_state()
        # New frame number → new context → must rebuild.
        overlay.set_context(RenderContext(frame=2, frame_total=10))
        second = overlay._pixmap_for_current_state()
        assert first is not second

    def test_template_change_rebuilds_pixmap(
        self, overlay: BurninOverlay,
    ) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        first = overlay._pixmap_for_current_state()
        # A second instance of the same builtin has different
        # object identity → cache signature differs → rebuild.
        overlay.set_template(builtin_template("default"))
        second = overlay._pixmap_for_current_state()
        assert first is not second

    def test_resize_invalidates_cache(
        self, overlay: BurninOverlay,
    ) -> None:
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        first = overlay._pixmap_for_current_state()
        overlay.resize(1280, 720)
        second = overlay._pixmap_for_current_state()
        # Pixmap rebuilt at the new size.
        assert first is not second
        assert second is not None
        # Some Qt platforms downscale pixmaps; check that the size at
        # least matches the new widget dimensions.
        assert second.size() == QSize(1280, 720)


class TestRenderQuality:
    """Pin the supersampling / HiDPI knobs of the overlay pixmap so
    text stays crisp when the viewport zooms out (small image rect)
    or when the user is on a HiDPI display (DPR > 1).

    The bug we're guarding against: a 1920×1080 plate fit-to-window
    in a 720p viewport gives a 720-px-tall image rect. Without
    oversampling, the renderer's typography scale (relative to a
    1000-px reference image) puts the font at ~5-6 px tall →
    rasterises blurry, gets bilinear-upscaled when the user
    full-screens, looks awful.
    """

    # The overlay subscribes to ``gl.transform_changed.connect`` and
    # calls ``gl.image_size`` / ``gl.current_transform`` on each
    # rebuild. A QObject with a real Signal is the minimum needed.
    from PySide6.QtCore import QObject, Signal

    class _MockGL(QObject):
        transform_changed = Signal()

        def __init__(self, w: int, h: int) -> None:
            super().__init__()
            self._w, self._h = w, h

        def image_size(self) -> tuple[int, int]:
            return (self._w, self._h)

        def current_transform(self) -> tuple[float, float, float]:
            # Fit at 50 % — the rect ends up at half the source size.
            return (0.5, 0.0, 0.0)

    def test_no_gl_means_no_oversampling(
        self, overlay: BurninOverlay,
    ) -> None:
        # Without a GL handle the overlay can't know the source
        # size, so scale collapses to 1.0 and the rasterised pixmap
        # matches the widget rect. Pin this so an over-eager refactor
        # doesn't silently grow allocations in headless tests.
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
        # Pixmap physical pixels = widget rect × dpr (≈1 in headless).
        # Logical size after setDevicePixelRatio should equal the
        # widget rect.
        dpr = max(1.0, float(overlay.devicePixelRatioF()))
        expected_w = int(round(800 * dpr))
        expected_h = int(round(450 * dpr))
        assert pix.size() == QSize(expected_w, expected_h)

    def test_source_larger_than_rect_oversamples(
        self, overlay: BurninOverlay, qtbot,
    ) -> None:
        # Attach a mock GL reporting a 1920×1080 source while the
        # overlay's rect is 800×450 (the fixture size). At fit-to-50%
        # the rect is half the source → we expect a 2× supersample.
        gl = self._MockGL(w=1920, h=1080)
        overlay.attach_gl_viewport(gl)
        # Move the widget into a 1920×1080-source context that
        # displays at half scale.
        overlay.resize(960, 540)  # rect ≈ source / 2
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
        # Physical pixmap size should be at least the SOURCE size in
        # the vertical axis (×dpr). Render is rect_h × scale × dpr
        # where scale = source_h / rect_h.
        dpr = max(1.0, float(overlay.devicePixelRatioF()))
        # rect_h comes from _compute_image_rect; with our mock's
        # 0.5 factor and a 1080-tall source it's 540. So the
        # supersample factor is 1080/540 = 2 → render_h ≈ 540 × 2 ×
        # dpr = 1080 × dpr.
        assert pix.height() >= int(round(1080 * dpr * 0.95)), (
            f"pixmap rasterised at {pix.height()} px — expected "
            f">= {int(round(1080 * dpr * 0.95))} (source-height "
            "oversampling regression)"
        )

    def test_oversample_capped(
        self, overlay: BurninOverlay,
    ) -> None:
        # An absurdly large source (16K plate displayed at 1080p)
        # must not allocate a 16K-tall burnin canvas — the cap keeps
        # render cost bounded. We pin the cap at 4× rect size.
        gl = self._MockGL(w=15360, h=8640)
        overlay.attach_gl_viewport(gl)
        overlay.resize(1920, 1080)
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
        # rect_h = 1080 × 0.5 = 540 (from the mock's 0.5 factor).
        # Cap is 4× rect_h × dpr.
        dpr = max(1.0, float(overlay.devicePixelRatioF()))
        cap = int(round(540 * 4 * dpr))
        assert pix.height() <= cap + 4, (
            f"pixmap height {pix.height()} px exceeded the 4× cap "
            f"({cap} px) — oversample knob is unbounded"
        )

    def test_logical_size_matches_rect(
        self, overlay: BurninOverlay,
    ) -> None:
        # Whatever the oversample factor, the pixmap's logical
        # device-independent size must equal the image rect — that's
        # what tells QPainter.drawPixmap to paint at the right
        # widget-coords extent.
        gl = self._MockGL(w=1920, h=1080)
        overlay.attach_gl_viewport(gl)
        overlay.resize(960, 540)
        overlay.set_template(builtin_template("default"))
        overlay.set_context(RenderContext(frame=1, frame_total=10))
        overlay.set_enabled(True)
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
        # deviceIndependentSize returns the pixmap's "logical"
        # size, i.e. physical_size / devicePixelRatio. The overlay
        # sets effective_dpr = scale × dpr so that
        # logical_size == rect_size — that's what QPainter.drawPixmap
        # needs to paint at the rect's widget-coords extent.
        # Tolerance covers float-rounding in the scale factor.
        di = pix.deviceIndependentSize()
        _, _, rect_w, rect_h = overlay._image_rect
        assert abs(di.width() - rect_w) <= 4
        assert abs(di.height() - rect_h) <= 4


class TestRobustness:
    def test_disabled_widget_has_no_paint_data(
        self, overlay: BurninOverlay,
    ) -> None:
        overlay.set_template(builtin_template("default"))
        # _enabled stays False — paintEvent early-returns. We can't
        # easily assert paintEvent did nothing, but we can verify the
        # widget itself never made itself visible.
        assert not overlay.isVisible()

    def test_template_with_both_bars_off_still_caches(
        self, overlay: BurninOverlay,
    ) -> None:
        # An all-disabled template paints nothing but should still
        # build a (mostly-transparent) pixmap and cache it — no crash.
        empty = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(enabled=False),
        )
        overlay.set_template(empty)
        overlay.set_enabled(True)
        pix = overlay._pixmap_for_current_state()
        assert pix is not None
