"""Pure-function tests for ``ChannelSelection`` and ``auto_grid``."""

from __future__ import annotations

from img_player.sequence.channels import (
    ChannelGroup,
    ChannelSelection,
    auto_grid,
)


# ----------------------------------------------------------------- helpers

def _g(label: str, *channels: str) -> ChannelGroup:
    return ChannelGroup(label=label, channels=channels)


# ----------------------------------------------------------------- ChannelSelection

class TestSelectionMode:
    def test_single_mode_when_no_tiles(self) -> None:
        sel = ChannelSelection(active=_g("RGB", "R", "G", "B"))
        assert not sel.is_contact_sheet
        assert sel.displayed == (_g("RGB", "R", "G", "B"),)

    def test_contact_sheet_mode_when_tiles_present(self) -> None:
        active = _g("RGB", "R", "G", "B")
        tiles = (_g("Z", "Z"), _g("albedo", "albedo.R", "albedo.G", "albedo.B"))
        sel = ChannelSelection(active=active, tiles=tiles)
        assert sel.is_contact_sheet
        # Active is *not* injected into displayed when tiles are set —
        # the user has explicitly opted into the tile selection.
        assert sel.displayed == tiles


class TestUnionChannels:
    def test_single_tile_passes_through(self) -> None:
        sel = ChannelSelection(active=_g("RGB", "R", "G", "B"))
        assert sel.union_channels() == ("R", "G", "B")

    def test_dedup_across_tiles(self) -> None:
        # Two tiles that share R/G/B (e.g. RGB beauty + a custom layer
        # that re-uses the bare R for some reason). Order preserved.
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(_g("RGB", "R", "G", "B"), _g("Z", "Z")),
        )
        assert sel.union_channels() == ("R", "G", "B", "Z")

    def test_preserves_first_seen_order(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(
                _g("normal", "N.X", "N.Y", "N.Z"),
                _g("RGB", "R", "G", "B"),
            ),
        )
        assert sel.union_channels() == ("N.X", "N.Y", "N.Z", "R", "G", "B")


class TestTileLayout:
    def test_single_tile_indexes_zero(self) -> None:
        sel = ChannelSelection(active=_g("RGB", "R", "G", "B"))
        assert sel.tile_layout() == (("RGB", (0, 1, 2)),)

    def test_multiple_tiles_share_indexes_when_channels_overlap(self) -> None:
        # Z appears once in the union; tiles that reference it both
        # point at the same column.
        sel = ChannelSelection(
            active=_g("Z", "Z"),
            tiles=(_g("Z", "Z"), _g("Z2", "Z")),
        )
        # Union has just one channel.
        assert sel.union_channels() == ("Z",)
        layout = sel.tile_layout()
        assert layout == (("Z", (0,)), ("Z2", (0,)))

    def test_disjoint_tiles_get_disjoint_columns(self) -> None:
        sel = ChannelSelection(
            active=_g("RGB", "R", "G", "B"),
            tiles=(
                _g("RGB", "R", "G", "B"),
                _g("normal", "N.X", "N.Y", "N.Z"),
            ),
        )
        assert sel.union_channels() == ("R", "G", "B", "N.X", "N.Y", "N.Z")
        assert sel.tile_layout() == (
            ("RGB", (0, 1, 2)),
            ("normal", (3, 4, 5)),
        )


# ----------------------------------------------------------------- auto_grid

class TestAutoGrid:
    def test_one_tile_is_one_by_one(self) -> None:
        assert auto_grid(1, viewport_aspect=16 / 9, tile_aspect=16 / 9) == (1, 1)

    def test_two_tiles_in_wide_viewport_go_horizontal(self) -> None:
        rows, cols = auto_grid(2, viewport_aspect=16 / 9, tile_aspect=16 / 9)
        assert (rows, cols) == (1, 2)

    def test_four_tiles_pick_two_by_two(self) -> None:
        rows, cols = auto_grid(4, viewport_aspect=16 / 9, tile_aspect=16 / 9)
        # 16:9 viewport with 16:9 tiles → cols ≈ √(4×1) = 2, rows = 2.
        assert rows * cols >= 4
        assert (rows, cols) == (2, 2)

    def test_nine_tiles_pick_three_by_three(self) -> None:
        rows, cols = auto_grid(9, viewport_aspect=16 / 9, tile_aspect=16 / 9)
        assert (rows, cols) == (3, 3)

    def test_tall_viewport_prefers_more_rows(self) -> None:
        # Portrait viewport, square tiles → cols < rows.
        rows, cols = auto_grid(6, viewport_aspect=9 / 16, tile_aspect=1.0)
        assert rows >= cols

    def test_wide_viewport_with_square_tiles_prefers_more_cols(self) -> None:
        rows, cols = auto_grid(6, viewport_aspect=21 / 9, tile_aspect=1.0)
        assert cols >= rows

    def test_zero_tiles_safe(self) -> None:
        # Caller still gets a valid (rows, cols) it can pass to a
        # numpy alloc without crashing.
        rows, cols = auto_grid(0, viewport_aspect=1.0, tile_aspect=1.0)
        assert rows >= 1 and cols >= 1

    def test_degenerate_aspect_safe(self) -> None:
        # Headless test where viewport hasn't been resized yet (h=0)
        # or the image hasn't loaded. We must not divide by zero.
        rows, cols = auto_grid(4, viewport_aspect=0.0, tile_aspect=0.0)
        assert rows * cols >= 4
