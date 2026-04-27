"""Tests for the cursor-anchored zoom math.

The pure function ``_anchored_pan_for_zoom`` lives at module level in
``img_player.render.gl_viewport`` precisely so the math can be tested
without a GL context — instantiating ``GLViewport`` would require a
QSurfaceFormat + QApplication, far heavier than what's needed to
verify the pan-on-zoom invariant.

The contract the function must uphold: after a zoom factor change,
the image-space pixel that was under the cursor must still be under
the cursor. We verify that by computing the cursor's image-space
coordinate from the inverse transform before and after the zoom and
asserting it's unchanged.
"""

from __future__ import annotations

import math

import pytest

from img_player.render.gl_viewport import _anchored_pan_for_zoom


def _cursor_image_coord(
    cursor_xy: tuple[float, float],
    widget_size: tuple[int, int],
    factor: float,
    pan: tuple[float, float],
) -> tuple[float, float]:
    """Inverse of the viewport's forward transform — what image-space
    offset (relative to image centre) sits under the given cursor."""
    cx, cy = cursor_xy
    win_w, win_h = widget_size
    px, py = pan
    return (
        (cx - win_w / 2.0 - px) / factor,
        (cy - win_h / 2.0 - py) / factor,
    )


# ---------------------------------------------------------------------- contract


class TestCursorPixelInvariant:
    """The whole point of the helper: the image-space pixel under the
    cursor before the zoom must land under the cursor after the zoom."""

    @pytest.mark.parametrize(
        ("cursor", "old_factor", "new_factor", "old_pan"),
        [
            # Cursor in upper-left, no pan, zoom in 2x — classic case.
            ((100.0, 100.0), 1.0, 2.0, (0.0, 0.0)),
            # Cursor near bottom-right, image already panned, zoom out.
            ((900.0, 700.0), 1.5, 0.6, (-30.0, 50.0)),
            # Fractional cursor (mac trackpad smooth scroll), arbitrary state.
            ((512.5, 384.25), 0.4321, 0.55, (12.3, -7.8)),
            # Cursor outside the widget bounds (edges of the viewport
            # clip the cursor in real Qt, but the math should still
            # behave).
            ((-50.0, 1200.0), 1.0, 1.5, (5.0, 5.0)),
        ],
    )
    def test_image_coord_under_cursor_is_invariant(
        self,
        cursor: tuple[float, float],
        old_factor: float,
        new_factor: float,
        old_pan: tuple[float, float],
    ) -> None:
        widget = (1024, 768)
        before = _cursor_image_coord(cursor, widget, old_factor, old_pan)
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=cursor,
            widget_size=widget,
            old_factor=old_factor,
            new_factor=new_factor,
            old_pan=old_pan,
        )
        after = _cursor_image_coord(cursor, widget, new_factor, new_pan)
        assert math.isclose(before[0], after[0], abs_tol=1e-9)
        assert math.isclose(before[1], after[1], abs_tol=1e-9)


# ---------------------------------------------------------------------- corollaries


class TestSpecialCases:
    """Sanity checks that make the helper's behaviour easy to reason
    about without running the math by hand."""

    def test_no_factor_change_means_no_pan_change(self) -> None:
        """If the factor is identical, the helper must be a no-op
        (otherwise floating-point drift would creep in over many
        unchanged wheel-but-clamped events)."""
        old_pan = (42.0, -17.0)
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=(100.0, 200.0),
            widget_size=(1024, 768),
            old_factor=1.5,
            new_factor=1.5,
            old_pan=old_pan,
        )
        assert new_pan == old_pan

    def test_cursor_at_image_centre_keeps_pan(self) -> None:
        """When the cursor sits exactly on the image's panned centre,
        the image centre is the anchor, so pan must stay put — the
        edge case that proves the formula doesn't drift the centre."""
        widget = (1024, 768)
        old_pan = (50.0, -30.0)
        # Cursor at (win_w/2 + pan_x, win_h/2 + pan_y) = image centre.
        cursor = (widget[0] / 2.0 + old_pan[0], widget[1] / 2.0 + old_pan[1])
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=cursor,
            widget_size=widget,
            old_factor=1.0,
            new_factor=2.0,
            old_pan=old_pan,
        )
        assert math.isclose(new_pan[0], old_pan[0], abs_tol=1e-9)
        assert math.isclose(new_pan[1], old_pan[1], abs_tol=1e-9)

    def test_cursor_at_widget_centre_with_zero_pan_stays_zero(self) -> None:
        """Fit-mode wheel from the dead centre: nothing should pan."""
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=(512.0, 384.0),
            widget_size=(1024, 768),
            old_factor=0.4,
            new_factor=0.6,
            old_pan=(0.0, 0.0),
        )
        assert new_pan == (0.0, 0.0)

    def test_zero_old_factor_is_a_noop(self) -> None:
        """Defensive: dividing by zero would crash. The viewport never
        produces ``factor == 0`` (clamped at MIN_ZOOM > 0), but we
        still guard the helper for any future caller."""
        old_pan = (10.0, 20.0)
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=(50.0, 50.0),
            widget_size=(800, 600),
            old_factor=0.0,
            new_factor=1.0,
            old_pan=old_pan,
        )
        assert new_pan == old_pan

    def test_zoom_in_at_corner_pushes_pan_outward(self) -> None:
        """A concrete case to lock in direction: cursor at the upper-
        left corner (well left of widget centre), zoom in 2x. The
        image should shift toward bottom-right so the upper-left
        pixel stays under the cursor."""
        new_pan = _anchored_pan_for_zoom(
            cursor_widget_xy=(0.0, 0.0),
            widget_size=(1000, 800),
            old_factor=1.0,
            new_factor=2.0,
            old_pan=(0.0, 0.0),
        )
        # u = 0 - 500 = -500; new = u - (u - 0)*2 = -500 + 1000 = 500
        # v = 0 - 400 = -400; new = -400 + 800 = 400
        assert math.isclose(new_pan[0], 500.0, abs_tol=1e-9)
        assert math.isclose(new_pan[1], 400.0, abs_tol=1e-9)
