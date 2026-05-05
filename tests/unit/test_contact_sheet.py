"""Pure-function tests for the CPU contact-sheet compositor."""

from __future__ import annotations

import numpy as np

from img_player.render.contact_sheet import (
    BG_FILL,
    GAP_PX,
    MAX_TILES,
    CompositeGeometry,
    TileRect,
    compose,
    plan_layout,
    tile_at,
)
from img_player.sequence.channels import ChannelGroup, ChannelSelection

# ----------------------------------------------------------------- helpers

def _g(label: str, *channels: str) -> ChannelGroup:
    return ChannelGroup(label=label, channels=channels)


def _union_buffer(h: int, w: int, n_channels: int) -> np.ndarray:
    """Build a deterministic test buffer: each channel filled with its
    own constant so we can verify per-tile slicing landed on the
    right columns. Channel ``i`` is filled with ``0.1 * (i + 1)``."""
    arr = np.empty((h, w, n_channels), dtype=np.float32)
    for i in range(n_channels):
        arr[:, :, i] = 0.1 * (i + 1)
    return arr


# ----------------------------------------------------------------- single mode

class TestSingleTile:
    def test_passes_through_when_no_tiles(self) -> None:
        sel = ChannelSelection(active=_g("RGB", "R", "G", "B"))
        buf = _union_buffer(64, 96, 3)
        out, _geom = compose(buf, sel, viewport_w=200, viewport_h=200)
        # No compositing in single mode → same shape, same content.
        assert out.shape == (64, 96, 3)
        np.testing.assert_allclose(out, buf)

    def test_broadcasts_single_channel_to_rgb(self) -> None:
        sel = ChannelSelection(active=_g("Z", "Z"))
        buf = _union_buffer(8, 8, 1)  # union has just Z
        out, _geom = compose(buf, sel, viewport_w=200, viewport_h=200)
        assert out.shape == (8, 8, 3)
        # All three channels should hold Z's constant.
        np.testing.assert_allclose(out[:, :, 0], 0.1)
        np.testing.assert_allclose(out[:, :, 1], 0.1)
        np.testing.assert_allclose(out[:, :, 2], 0.1)


# ----------------------------------------------------------------- contact-sheet

class TestContactSheet:
    def test_two_tiles_lay_side_by_side(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(_g("RGB", "R", "G", "B"), _g("Z", "Z")),
        )
        # Union: R, G, B, Z (4 channels).
        buf = _union_buffer(40, 60, 4)
        out, _geom = compose(buf, sel, viewport_w=400, viewport_h=200)
        # Wide viewport with 16:9 tiles → 1×2 grid.
        # Output should be at least taller/wider than a single tile.
        assert out.ndim == 3
        assert out.shape[2] == 3
        # Composite should be wider than one tile + gap.
        assert out.shape[1] >= 2 * 1 + 3 * GAP_PX

    def test_two_tiles_carry_correct_channels(self) -> None:
        # Verify each tile pulls the right slice of the union: tile 0
        # = RGB (channels 0/1/2), tile 1 = Z (channel 3 broadcast).
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(_g("RGB", "R", "G", "B"), _g("Z", "Z")),
        )
        # Big enough that downsample factor is 1 (no aliasing of the
        # constant-fill assertions below).
        buf = _union_buffer(20, 20, 4)
        out, _geom = compose(buf, sel, viewport_w=400, viewport_h=200)
        # Find non-background pixels: any pixel whose RGB doesn't equal BG.
        bg = np.array(BG_FILL, dtype=out.dtype)
        non_bg_mask = np.any(out != bg, axis=2)
        # At least some pixels should be tile content.
        assert non_bg_mask.any()
        # Tile-0 pixels (RGB) must have R≈0.1, G≈0.2, B≈0.3.
        # Tile-1 pixels (Z) must have R=G=B=0.4 (broadcast).
        tile_pixels = out[non_bg_mask]
        # Either match RGB tile (0.1, 0.2, 0.3) or Z tile (0.4, 0.4, 0.4).
        is_rgb_tile = np.all(np.isclose(tile_pixels, [0.1, 0.2, 0.3], atol=1e-4), axis=1)
        is_z_tile = np.all(np.isclose(tile_pixels, [0.4, 0.4, 0.4], atol=1e-4), axis=1)
        assert (is_rgb_tile | is_z_tile).all(), \
            "every non-background pixel should belong to exactly one of the two tiles"
        # Both tiles must be present.
        assert is_rgb_tile.any()
        assert is_z_tile.any()

    def test_caps_at_max_tiles(self) -> None:
        # Build a selection with more than MAX_TILES groups; the
        # compositor must silently clamp rather than crash.
        many = tuple(_g(f"L{i}", f"chan{i}") for i in range(MAX_TILES + 4))
        # Channel names are unique → union size = len(many).
        n = len(many)
        buf = _union_buffer(8, 8, n)
        sel = ChannelSelection(active=many[0], tiles=many)
        out, _geom = compose(buf, sel, viewport_w=400, viewport_h=400)
        # Output is non-empty and well-shaped.
        assert out.ndim == 3 and out.shape[2] == 3


# ----------------------------------------------------------------- plan_layout

# ----------------------------------------------------------------- geometry + tile_at

class TestGeometry:
    def test_compose_returns_tile_rects(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(_g("RGB", "R", "G", "B"), _g("Z", "Z")),
        )
        buf = _union_buffer(20, 20, 4)
        out, geom = compose(buf, sel, viewport_w=400, viewport_h=200)
        # 1×2 layout (wide vp, square tiles).
        assert geom.rows * geom.cols >= 2
        # Two tile rects, ordered by (row, col) = the same order as
        # selection.tiles.
        assert len(geom.tiles) == 2
        assert {t.label for t in geom.tiles} == {"RGB", "Z"}
        # Composite size matches geometry advertisement.
        assert out.shape[0] == geom.composite_h
        assert out.shape[1] == geom.composite_w

    def test_tile_rects_inside_composite_bounds(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=tuple(_g(f"L{i}", f"c{i}") for i in range(4)),
        )
        buf = _union_buffer(8, 8, 4)
        out, geom = compose(buf, sel, viewport_w=400, viewport_h=400)
        for rect in geom.tiles:
            assert 0 <= rect.x < geom.composite_w
            assert 0 <= rect.y < geom.composite_h
            assert rect.x + rect.w <= geom.composite_w
            assert rect.y + rect.h <= geom.composite_h


class TestTileAt:
    def test_inside_first_tile(self) -> None:
        geom = CompositeGeometry(
            rows=1, cols=2, composite_w=200, composite_h=100,
            tiles=(
                TileRect("RGB", x=0, y=0, w=100, h=100),
                TileRect("Z", x=100, y=0, w=100, h=100),
            ),
        )
        assert tile_at(geom, 50, 50) == "RGB"

    def test_inside_second_tile(self) -> None:
        geom = CompositeGeometry(
            rows=1, cols=2, composite_w=200, composite_h=100,
            tiles=(
                TileRect("RGB", x=0, y=0, w=100, h=100),
                TileRect("Z", x=100, y=0, w=100, h=100),
            ),
        )
        assert tile_at(geom, 150, 50) == "Z"

    def test_outside_returns_none(self) -> None:
        geom = CompositeGeometry(
            rows=1, cols=1, composite_w=100, composite_h=100,
            tiles=(TileRect("RGB", x=0, y=0, w=100, h=100),),
        )
        assert tile_at(geom, -5, 50) is None
        assert tile_at(geom, 150, 50) is None
        assert tile_at(geom, 50, -5) is None

    def test_empty_geometry_returns_none(self) -> None:
        geom = CompositeGeometry(
            rows=1, cols=1, composite_w=10, composite_h=10, tiles=(),
        )
        assert tile_at(geom, 5, 5) is None


class TestPlanLayout:
    def test_returns_correct_count_of_specs(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(_g("RGB", "R", "G", "B"), _g("Z", "Z"), _g("N", "N.X")),
        )
        rows, cols, specs = plan_layout(
            sel, viewport_w=400, viewport_h=300, tile_w=100, tile_h=100,
        )
        assert len(specs) == 3
        assert rows * cols >= 3
        # Specs ordered row-major.
        for i, spec in enumerate(specs):
            assert spec.row == i // cols
            assert spec.col == i % cols

    def test_caps_at_max_tiles(self) -> None:
        many = tuple(_g(f"L{i}", f"c{i}") for i in range(MAX_TILES + 4))
        sel = ChannelSelection(active=many[0], tiles=many)
        _, _, specs = plan_layout(sel, viewport_w=400, viewport_h=400, tile_w=100, tile_h=100)
        assert len(specs) == MAX_TILES
