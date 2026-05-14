"""Tests for :class:`ContactSheetState.effective_grid`.

Covers the four resolution paths:

* both ``cols`` and ``rows`` set → manual grid returned verbatim;
* one dim set, the other ``None`` → axis preset, missing dim auto-
  fits as ``ceil(n_layers / fixed)``;
* both ``None`` → routes to :func:`auto_grid_dimensions` (square-ish
  fallback when no ``canvas_aspect``).

The axis-preset case is the focus of the recent menu redesign:
"1 row / 2 rows / N columns" replaced the old fixed "1×2 / 2×3 /
4×4" presets which silently dropped layers past ``cols × rows`` when
the stack had more than the preset's cell count.
"""

from __future__ import annotations

import math

import pytest

from img_player.contact_sheet.state import ContactSheetState


# ============================================================================
# Manual grid (both dims set) — returned verbatim
# ============================================================================


class TestManualGrid:
    def test_manual_returns_exact_pair(self) -> None:
        state = ContactSheetState(enabled=True, cols=4, rows=4)
        assert state.effective_grid(
            n_layers=5, image_aspect=16 / 9,
        ) == (4, 4)

    def test_manual_does_not_grow_for_more_layers(self) -> None:
        """The "I want exactly this layout" mode opts into dropping
        trailing layers past cols × rows — the user picked the cells
        explicitly so we respect that."""
        state = ContactSheetState(enabled=True, cols=2, rows=2)
        assert state.effective_grid(
            n_layers=20, image_aspect=16 / 9,
        ) == (2, 2)


# ============================================================================
# Axis preset (one dim set) — missing dim auto-fits to n_layers
# ============================================================================


class TestAxisPreset:
    @pytest.mark.parametrize(
        "n_layers,rows,expected_cols",
        [
            (1, 1, 1),   # 1 layer, 1 row → 1 col
            (5, 1, 5),   # 5 layers, 1 row → 5 cols
            (5, 2, 3),   # 5 layers, 2 rows → ceil(5/2) = 3 cols
            (5, 3, 2),   # 5 layers, 3 rows → ceil(5/3) = 2 cols
            (8, 4, 2),   # 8 layers, 4 rows → 2 cols (exact)
            (9, 4, 3),   # 9 layers, 4 rows → ceil(9/4) = 3 cols
        ],
    )
    def test_rows_pinned_cols_autofit(
        self, n_layers: int, rows: int, expected_cols: int,
    ) -> None:
        """``rows = R, cols = None`` → cols = ceil(n / R) so every
        layer gets a tile, with the trailing row possibly partial."""
        state = ContactSheetState(enabled=True, cols=None, rows=rows)
        cols, got_rows = state.effective_grid(
            n_layers=n_layers, image_aspect=16 / 9,
        )
        assert cols == expected_cols
        assert got_rows == rows

    @pytest.mark.parametrize(
        "n_layers,cols,expected_rows",
        [
            (1, 1, 1),
            (5, 1, 5),
            (5, 2, 3),
            (5, 3, 2),
            (8, 4, 2),
            (9, 4, 3),
        ],
    )
    def test_cols_pinned_rows_autofit(
        self, n_layers: int, cols: int, expected_rows: int,
    ) -> None:
        """Symmetric to the rows-pinned case."""
        state = ContactSheetState(enabled=True, cols=cols, rows=None)
        got_cols, rows = state.effective_grid(
            n_layers=n_layers, image_aspect=16 / 9,
        )
        assert got_cols == cols
        assert rows == expected_rows

    def test_axis_preset_clamps_to_one(self) -> None:
        """``n_layers = 0`` doesn't crash — the grid math floors to 1
        so the composite still has a single 1×1 cell."""
        state = ContactSheetState(enabled=True, cols=None, rows=2)
        cols, rows = state.effective_grid(
            n_layers=0, image_aspect=16 / 9,
        )
        assert cols >= 1
        assert rows == 2


# ============================================================================
# Auto (both None) — routes to auto_grid_dimensions
# ============================================================================


class TestAutoGrid:
    def test_auto_without_canvas_aspect_is_square_ish(self) -> None:
        """No canvas hint = classic ceil(sqrt(n)) square grid."""
        state = ContactSheetState(enabled=True)
        cols, rows = state.effective_grid(
            n_layers=4, image_aspect=16 / 9,
        )
        # 4 layers → 2×2; the exact shape comes from auto_grid_dimensions.
        assert cols * rows >= 4
        # Square-ish: side length should be about sqrt(n).
        expected = math.ceil(math.sqrt(4))
        assert cols <= expected + 1
        assert rows <= expected + 1

    def test_auto_with_canvas_aspect_routes_to_smart_grid(self) -> None:
        """With a canvas hint the smart-grid pick maximises per-tile
        area. We don't pin the exact (cols, rows) here — that's the
        compose helper's concern — just that the result covers all
        layers."""
        state = ContactSheetState(enabled=True)
        cols, rows = state.effective_grid(
            n_layers=6, image_aspect=16 / 9, canvas_aspect=16 / 9,
        )
        assert cols * rows >= 6
