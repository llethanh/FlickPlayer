"""Tests for :class:`img_player.ui.channel_menu.ChannelMenu`.

The menu is mostly UI but its public contract drives both the cache
(via ``selection_changed`` → ``set_channel_selection``) and the
contact-sheet shape (via ``layout_mode_changed``). Pin those signals
behaviours so future cosmetic refactors can't silently break the
controller wiring.
"""

from __future__ import annotations

import pytest

from img_player.sequence.channels import ChannelGroup, ChannelSelection
from img_player.ui.channel_menu import (
    DEFAULT_LAYOUT_MODE,
    LAYOUT_MODES,
    ChannelMenu,
)


# ============================================================================
# Fixture: a ChannelMenu primed with the four canonical groups.
# ============================================================================


@pytest.fixture
def groups() -> list[ChannelGroup]:
    return [
        ChannelGroup(label="RGB", channels=("R", "G", "B")),
        ChannelGroup(label="albedo", channels=("albedo.R", "albedo.G", "albedo.B")),
        ChannelGroup(label="Z", channels=("Z",)),
        ChannelGroup(label="N", channels=("N.X", "N.Y", "N.Z")),
    ]


@pytest.fixture
def menu(qtbot, groups: list[ChannelGroup]) -> ChannelMenu:
    m = ChannelMenu()
    qtbot.addWidget(m)
    m.set_groups(groups)
    return m


# ============================================================================
# Initial state
# ============================================================================


class TestInitialState:
    def test_first_group_is_active_by_default(self, menu: ChannelMenu) -> None:
        assert menu.active_label == "RGB"

    def test_no_tiles_checked_initially(self, menu: ChannelMenu) -> None:
        assert menu.tile_labels == ()

    def test_layout_mode_defaults_to_auto(self, menu: ChannelMenu) -> None:
        assert menu.layout_mode == DEFAULT_LAYOUT_MODE

    def test_current_selection_is_single_mode_rgb(self, menu: ChannelMenu) -> None:
        sel = menu.current_selection()
        assert sel is not None
        assert sel.active.label == "RGB"
        assert not sel.is_contact_sheet


# ============================================================================
# Programmatic state restore (no signals)
# ============================================================================


class TestSetState:
    def test_restore_active_label(self, menu: ChannelMenu) -> None:
        menu.set_state(active="albedo", tiles=())
        assert menu.active_label == "albedo"

    def test_restore_tiles(self, menu: ChannelMenu) -> None:
        menu.set_state(active="RGB", tiles=("Z", "N"))
        assert set(menu.tile_labels) == {"Z", "N"}
        sel = menu.current_selection()
        assert sel is not None
        assert sel.is_contact_sheet
        assert {t.label for t in sel.tiles} == {"Z", "N"}

    def test_unknown_label_silently_dropped(self, menu: ChannelMenu) -> None:
        # Saved state from a different sequence — labels that don't
        # exist now must NOT crash and must NOT pollute tile_labels.
        menu.set_state(active="ghost_aov", tiles=("RGB", "vanished_layer"))
        assert menu.active_label == "RGB"  # unchanged, since ghost_aov absent
        assert menu.tile_labels == ("RGB",)  # only the surviving label

    def test_restore_layout_mode(self, menu: ChannelMenu) -> None:
        menu.set_state(active="RGB", tiles=(), layout_mode="2×2")
        assert menu.layout_mode == "2×2"

    def test_invalid_layout_mode_ignored(self, menu: ChannelMenu) -> None:
        menu.set_state(active="RGB", tiles=(), layout_mode="bogus")
        assert menu.layout_mode == DEFAULT_LAYOUT_MODE

    def test_set_state_does_not_emit(self, menu: ChannelMenu, qtbot) -> None:
        # Restore from prefs must NOT trigger selection_changed —
        # the controller already knows the saved state.
        with qtbot.assertNotEmitted(menu.selection_changed):
            menu.set_state(active="albedo", tiles=("Z",))


# ============================================================================
# Reset behaviour
# ============================================================================


class TestReset:
    def test_reset_clears_all_tiles(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        menu.set_state(active="RGB", tiles=("albedo", "Z"))
        # Reset is invoked through the footer click — but we exercise
        # the same internal path the user click would take.
        menu._on_reset_clicked()  # type: ignore[attr-defined]
        assert menu.tile_labels == ()

    def test_reset_keeps_active_radio(self, menu: ChannelMenu) -> None:
        menu.set_state(active="albedo", tiles=("Z", "N"))
        menu._on_reset_clicked()  # type: ignore[attr-defined]
        # User stays on albedo, just no longer in contact-sheet mode.
        assert menu.active_label == "albedo"

    def test_reset_emits_selection_changed_when_tiles_were_set(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        menu.set_state(active="RGB", tiles=("Z",))
        with qtbot.waitSignal(menu.selection_changed, timeout=200) as sig:
            menu._on_reset_clicked()  # type: ignore[attr-defined]
        # Carrier is a ChannelSelection — controller will switch back
        # to single-mode based on its is_contact_sheet flag.
        emitted = sig.args[0]
        assert isinstance(emitted, ChannelSelection)
        assert not emitted.is_contact_sheet

    def test_reset_when_already_empty_does_not_emit(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        # Idempotent: clicking Reset on an already-clean menu is a no-op.
        with qtbot.assertNotEmitted(menu.selection_changed):
            menu._on_reset_clicked()  # type: ignore[attr-defined]


# ============================================================================
# Layout mode changes
# ============================================================================


class TestLayoutMode:
    def test_layout_picked_emits_signal(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        with qtbot.waitSignal(menu.layout_mode_changed, timeout=200) as sig:
            menu._on_layout_picked("2×2")  # type: ignore[attr-defined]
        assert sig.args == ["2×2"]
        assert menu.layout_mode == "2×2"

    def test_same_layout_picked_does_not_re_emit(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        # Layout combo's own currentTextChanged would fire even when
        # the user re-picks the same mode; we squelch that.
        menu._on_layout_picked("2×2")  # type: ignore[attr-defined]
        with qtbot.assertNotEmitted(menu.layout_mode_changed):
            menu._on_layout_picked("2×2")  # type: ignore[attr-defined]

    def test_all_layout_tokens_accepted(self, menu: ChannelMenu) -> None:
        # The constants in LAYOUT_MODES must round-trip through the
        # menu without being rejected.
        for mode in LAYOUT_MODES:
            menu._on_layout_picked(mode)  # type: ignore[attr-defined]
            assert menu.layout_mode == mode


# ============================================================================
# Selection changes via direct row interaction
# ============================================================================


class TestSelectionEmissions:
    def test_check_toggle_emits_with_tile(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        with qtbot.waitSignal(menu.selection_changed, timeout=200) as sig:
            menu._on_check_toggled("albedo", True)  # type: ignore[attr-defined]
        sel = sig.args[0]
        assert sel.is_contact_sheet
        assert "albedo" in {t.label for t in sel.tiles}

    def test_uncheck_returns_to_single_mode(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        menu._on_check_toggled("albedo", True)  # type: ignore[attr-defined]
        with qtbot.waitSignal(menu.selection_changed, timeout=200) as sig:
            menu._on_check_toggled("albedo", False)  # type: ignore[attr-defined]
        sel = sig.args[0]
        assert not sel.is_contact_sheet

    def test_radio_pick_emits_new_active(
        self, menu: ChannelMenu, qtbot
    ) -> None:
        with qtbot.waitSignal(menu.selection_changed, timeout=200) as sig:
            menu._on_radio_picked("Z")  # type: ignore[attr-defined]
        sel = sig.args[0]
        assert sel.active.label == "Z"

    def test_tile_order_follows_group_order(
        self, menu: ChannelMenu
    ) -> None:
        # Even if the user checks Z then albedo, the resulting tile
        # tuple respects the original group order so the contact
        # sheet's grid is stable.
        menu._on_check_toggled("Z", True)  # type: ignore[attr-defined]
        menu._on_check_toggled("albedo", True)  # type: ignore[attr-defined]
        sel = menu.current_selection()
        assert sel is not None
        assert [t.label for t in sel.tiles] == ["albedo", "Z"]


# ============================================================================
# Empty / edge cases
# ============================================================================


class TestEmpty:
    def test_empty_groups_no_selection(self, qtbot) -> None:
        m = ChannelMenu()
        qtbot.addWidget(m)
        m.set_groups([])
        assert m.current_selection() is None
        assert m.active_label == ""
        assert m.tile_labels == ()

    def test_set_groups_resets_state(
        self, menu: ChannelMenu
    ) -> None:
        # Pre-load some state.
        menu.set_state(active="albedo", tiles=("Z", "N"), layout_mode="2×2")
        # Loading a new sequence's groups must clear tiles + put
        # the active radio on the first group of the new list.
        menu.set_groups([
            ChannelGroup(label="OnlyOne", channels=("R", "G", "B")),
        ])
        assert menu.active_label == "OnlyOne"
        assert menu.tile_labels == ()
        # Layout mode is intentionally PRESERVED across sequence
        # reloads — it's a UI preference, not per-sequence state.
        assert menu.layout_mode == "2×2"
