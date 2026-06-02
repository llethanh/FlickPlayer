"""Tests for per-tile burnins in the contact-sheet composer.

The burnin is baked INTO the composite (vs an overlay widget on the
live single-image path) because the CS uploads a single texture to
the GL viewport. We pin:

* The new ``burnin_template`` / ``per_tile_burnin_contexts`` kwargs
  on :func:`render_contact_sheet` keep their default ``None`` and
  the function produces the same output as before — no regression
  for callers that don't opt in.
* When the kwargs ARE provided, each tile's slice shows
  pixel differences relative to the no-burnin baseline.
* Tile-specific context: tile 0 and tile 1 produce DIFFERENT pixels
  because their layer names / frame numbers differ — the per-tile
  context actually drives the rendering (not a global one).
* The helper handles both float and uint8 tile dtypes.
"""

from __future__ import annotations

import numpy as np

from img_player.burnins.builtins import builtin_template
from img_player.burnins.tokens import RenderContext
from img_player.contact_sheet.compose import (
    _paint_burnin_overlay,
    render_contact_sheet,
)


# ---------------------------------------------------------------------- Helpers

def _solid_tile(h: int = 240, w: int = 320, *, level: float = 0.4) -> np.ndarray:
    """A solid mid-grey float tile, the dtype the CS pipeline uses."""
    return np.full((h, w, 4), (level, level, level, 1.0), dtype=np.float32)


def _ctx_for_tile(layer_name: str, frame: int) -> RenderContext:
    return RenderContext(
        frame=frame,
        frame_total=1244,
        fps=24.0,
        width=1920,
        height=1080,
        sequence="seq.####.exr",
        layer_name=layer_name,
        date="2026-05-27",
        user="reviewer",
    )


# ---------------------------------------------------------------------- _paint_burnin_overlay

class TestPaintBurninOverlay:
    def test_paints_dark_bar_pixels_at_top(self) -> None:
        # The default Dailies template has a dark semi-transparent
        # top bar — the top rows of the tile must darken after the
        # call.
        tile = _solid_tile()
        before = tile.copy()
        _paint_burnin_overlay(
            tile,
            template=builtin_template("default"),
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        # First 16 rows should differ from the input.
        assert not np.array_equal(tile[:16, :, :3], before[:16, :, :3])

    def test_preserves_alpha_channel(self) -> None:
        # The renderer alpha-composites bars onto the RGB channels
        # but never touches the tile's alpha — important for layers
        # using straight-alpha that get composited downstream.
        tile = _solid_tile()
        _paint_burnin_overlay(
            tile,
            template=builtin_template("default"),
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        # Alpha = 1.0 everywhere as input — must stay 1.0.
        assert np.allclose(tile[..., 3], 1.0)

    def test_handles_uint8_dtype(self) -> None:
        # CS can also produce uint8 tiles when the input layers are
        # uint8 (e.g. PNG / JPG without HDR). Burnin must still work.
        tile = np.full((240, 320, 4), (102, 102, 102, 255), dtype=np.uint8)
        before = tile.copy()
        _paint_burnin_overlay(
            tile,
            template=builtin_template("default"),
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        assert not np.array_equal(tile[:16, :, :3], before[:16, :, :3])
        assert tile.dtype == np.uint8

    def test_no_template_is_noop(self) -> None:
        tile = _solid_tile()
        before = tile.copy()
        _paint_burnin_overlay(
            tile,
            template=None,
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        assert np.array_equal(tile, before)

    def test_no_context_is_noop(self) -> None:
        tile = _solid_tile()
        before = tile.copy()
        _paint_burnin_overlay(
            tile,
            template=builtin_template("default"),
            context=None,
            n_channels=4,
            dtype=tile.dtype,
        )
        assert np.array_equal(tile, before)

    def test_both_bars_disabled_skips_all_work(self) -> None:
        # Perf early-out: when the active template has neither bar
        # enabled, _paint_burnin_overlay must short-circuit before
        # any allocation or renderer call. Pinned by checking the
        # tile is bit-identical and the scratch cache is NOT
        # populated for these dims by this call.
        from img_player.burnins.model import BurninBar, BurninTemplate
        from img_player.contact_sheet.compose import _BURNIN_SCRATCH
        empty_tpl = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(enabled=False),
        )
        # Use a tile shape that's unlikely to collide with other
        # tests' scratch keys.
        tile = _solid_tile(h=137, w=211)
        before = tile.copy()
        _BURNIN_SCRATCH.pop((137, 211), None)
        _paint_burnin_overlay(
            tile,
            template=empty_tpl,
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        assert np.array_equal(tile, before), "tile must not change"
        assert (137, 211) not in _BURNIN_SCRATCH, (
            "scratch buffer was allocated for an all-bars-off template "
            "— the early-out is broken"
        )

    def test_middle_of_tile_is_not_touched(self) -> None:
        # The bars only paint the top + bottom strips. The center
        # rows must be pixel-identical to the input, even though
        # the float-blend path runs. This pins the row-bounded
        # blend optimisation: if the optimisation regresses to
        # blending the full tile, rounding-trip through float32 +
        # astype would alter the center pixels by 1 LSB and this
        # would catch it.
        tile = np.full(
            (240, 320, 4),
            (200, 200, 200, 255),  # bright grey so any blend stands out
            dtype=np.uint8,
        )
        before = tile.copy()
        _paint_burnin_overlay(
            tile,
            template=builtin_template("default"),
            context=_ctx_for_tile("plate", 42),
            n_channels=4,
            dtype=tile.dtype,
        )
        # Sample the middle 60 % of the tile vertically — well
        # outside any reasonable bar region.
        mid_lo, mid_hi = int(240 * 0.3), int(240 * 0.7)
        assert np.array_equal(
            tile[mid_lo:mid_hi, :, :3], before[mid_lo:mid_hi, :, :3]
        ), "middle of tile was modified — row-bounded blend regression"

    def test_scratch_buffer_is_reused_across_calls(self) -> None:
        # Reusing the scratch keeps heap pressure flat across N
        # tiles of the same size. Pin: after two calls at the same
        # dims, _BURNIN_SCRATCH holds exactly one entry for that
        # shape and it's the SAME ndarray (object identity).
        from img_player.contact_sheet.compose import _BURNIN_SCRATCH
        # Use a unique shape to avoid leakage from other tests.
        h, w = 211, 313
        _BURNIN_SCRATCH.clear()
        tile_a = _solid_tile(h=h, w=w)
        tile_b = _solid_tile(h=h, w=w)
        _paint_burnin_overlay(
            tile_a,
            template=builtin_template("default"),
            context=_ctx_for_tile("a", 1),
            n_channels=4, dtype=tile_a.dtype,
        )
        buf_after_first = _BURNIN_SCRATCH[(h, w)]
        _paint_burnin_overlay(
            tile_b,
            template=builtin_template("default"),
            context=_ctx_for_tile("b", 2),
            n_channels=4, dtype=tile_b.dtype,
        )
        buf_after_second = _BURNIN_SCRATCH[(h, w)]
        # Same object — buffer was reused, not re-allocated.
        assert buf_after_first is buf_after_second

    def test_scratch_evicts_on_shape_change(self) -> None:
        # When the user resizes the CS viewport, tile dims change.
        # The cache must drop the old buffer to avoid growing
        # unboundedly — at most one buffer cached at a time.
        from img_player.contact_sheet.compose import _BURNIN_SCRATCH
        _BURNIN_SCRATCH.clear()
        tile_a = _solid_tile(h=120, w=160)
        tile_b = _solid_tile(h=180, w=240)
        _paint_burnin_overlay(
            tile_a,
            template=builtin_template("default"),
            context=_ctx_for_tile("a", 1),
            n_channels=4, dtype=tile_a.dtype,
        )
        _paint_burnin_overlay(
            tile_b,
            template=builtin_template("default"),
            context=_ctx_for_tile("b", 1),
            n_channels=4, dtype=tile_b.dtype,
        )
        # Only the latest shape's buffer should remain.
        assert set(_BURNIN_SCRATCH.keys()) == {(180, 240)}


# ---------------------------------------------------------------------- render_contact_sheet

class TestRenderContactSheetWithBurnins:
    def test_no_burnin_kwargs_unchanged_output(self) -> None:
        # Back-compat pin: a caller that doesn't pass the burnin
        # kwargs gets exactly the same composite as before. Compare
        # two renders, one without args, one with args explicitly None.
        tiles = [_solid_tile() for _ in range(2)]
        baseline = render_contact_sheet(
            tiles, names=["a", "b"], cols=2, rows=1,
            target_w=640, target_h=240,
        )
        with_explicit_none = render_contact_sheet(
            tiles, names=["a", "b"], cols=2, rows=1,
            target_w=640, target_h=240,
            burnin_template=None,
            per_tile_burnin_contexts=None,
        )
        assert np.array_equal(baseline, with_explicit_none)

    def test_burnin_kwargs_modify_each_tile(self) -> None:
        tiles = [_solid_tile() for _ in range(2)]
        ctxs = [
            _ctx_for_tile("plate", 100),
            _ctx_for_tile("beauty", 200),
        ]
        baseline = render_contact_sheet(
            tiles, names=["plate", "beauty"], cols=2, rows=1,
            target_w=640, target_h=240,
        )
        with_burnin = render_contact_sheet(
            tiles, names=["plate", "beauty"], cols=2, rows=1,
            target_w=640, target_h=240,
            burnin_template=builtin_template("default"),
            per_tile_burnin_contexts=ctxs,
        )
        # Tile 0 region (left half) differs.
        assert not np.array_equal(
            baseline[:, :320, :], with_burnin[:, :320, :],
        )
        # Tile 1 region (right half) also differs.
        assert not np.array_equal(
            baseline[:, 320:, :], with_burnin[:, 320:, :],
        )

    def test_per_tile_contexts_produce_different_tiles(self) -> None:
        # The point of per-tile contexts: each tile carries its own
        # layer name / frame / sequence. Tile 0 with name "plate"
        # frame 100 should produce different pixels than tile 1 with
        # name "beauty" frame 200 (different text → different glyphs).
        # We pin "the tiles differ" — not the exact pixel content,
        # which depends on font rendering.
        tiles = [_solid_tile() for _ in range(2)]
        ctxs = [
            _ctx_for_tile("plate_v001_aaaaaa", 1),
            _ctx_for_tile("beauty_v007", 9999),
        ]
        out = render_contact_sheet(
            tiles, names=["a", "b"], cols=2, rows=1,
            target_w=640, target_h=240,
            burnin_template=builtin_template("default"),
            per_tile_burnin_contexts=ctxs,
        )
        # Top bar area of each tile — text differs → pixels differ.
        assert not np.array_equal(out[:24, :320, :3], out[:24, 320:, :3])

    def test_none_context_in_list_skips_that_tile_only(self) -> None:
        # A caller might want to skip the burnin on a specific tile
        # (e.g. an out-of-range layer) — passing ``None`` in the
        # list at that index leaves the tile alone but still draws
        # the burnin on the others.
        tiles = [_solid_tile() for _ in range(2)]
        ctxs = [None, _ctx_for_tile("beauty", 200)]
        baseline = render_contact_sheet(
            tiles, names=["a", "b"], cols=2, rows=1,
            target_w=640, target_h=240,
        )
        with_burnin = render_contact_sheet(
            tiles, names=["a", "b"], cols=2, rows=1,
            target_w=640, target_h=240,
            burnin_template=builtin_template("default"),
            per_tile_burnin_contexts=ctxs,
        )
        # Tile 0 (None context) unchanged.
        assert np.array_equal(
            baseline[:, :320, :], with_burnin[:, :320, :],
        )
        # Tile 1 (context present) modified.
        assert not np.array_equal(
            baseline[:, 320:, :], with_burnin[:, 320:, :],
        )
