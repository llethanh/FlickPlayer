"""Tests for the View → Active burnin template submenu state.

Pin the exclusivity contract: at most ONE entry in the submenu is
checked at any time. This was reported as a real bug — after saving
a custom template via the editor, the menu showed both the new
template AND the previous one (e.g. ``dailies_default``) ticked
simultaneously, because the sync path used ``blockSignals(True)``
around ``setChecked(True)`` to suppress the slug-pick signal — and
that same signal is what QActionGroup listens to for its
exclusivity machinery, so the previous-active row never got
auto-unchecked.

The fix replaces the naive setter with a silent helper that
explicitly unchecks every sibling before checking the target. These
tests pin that contract from the public window API so a future
refactor can't quietly drop it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from img_player.color.ocio_manager import OCIOManager
from img_player.comment.store import CommentStore
from img_player.ui.main_window import MainWindow


@pytest.fixture
def main_window(qtbot) -> MainWindow:  # type: ignore[no-untyped-def]
    ocio = MagicMock(spec=OCIOManager)
    ocio.list_colorspaces.return_value = ["scene_linear", "sRGB"]
    ocio.list_displays.return_value = ["sRGB"]
    ocio.list_views.return_value = ["ACES 1.0 SDR-video"]
    ocio.default_display.return_value = "sRGB"
    ocio.default_view.return_value = "ACES 1.0 SDR-video"
    ocio.role.return_value = "scene_linear"
    window = MainWindow(ocio, CommentStore())
    qtbot.addWidget(window)
    return window


def _checked_slugs(window: MainWindow) -> list[str]:
    """All slugs whose menu action is currently ticked."""
    return [
        slug for slug, act in window._burnin_template_actions.items()
        if act.isChecked()
    ]


class TestExclusivity:
    def test_refresh_leaves_exactly_one_check(
        self, main_window: MainWindow,
    ) -> None:
        main_window.refresh_burnin_template_menu(
            ["default", "minimal", "studio_banner"],
            "default",
        )
        assert _checked_slugs(main_window) == ["default"]

    def test_set_state_after_refresh_to_different_slug_unticks_old(
        self, main_window: MainWindow,
    ) -> None:
        # This is the exact sequence that produced the bug in the
        # field: refresh with dailies_default active, then sync the
        # state to a NEW slug (e.g. user just saved "test01" via the
        # editor). The submenu must not retain the dailies_default
        # tick.
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal", "studio_banner"],
            "default",
        )
        assert _checked_slugs(main_window) == ["default"]
        main_window.set_burnin_menu_state(True, "test01")
        # Only test01 ticked — NOT both.
        assert _checked_slugs(main_window) == ["test01"], (
            "two burnin templates were checked simultaneously — "
            "QActionGroup exclusivity got bypassed by the signal "
            "blocking around setChecked"
        )

    def test_repeated_set_state_stays_single(
        self, main_window: MainWindow,
    ) -> None:
        # Swap several times, each must leave a single tick.
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal", "studio_banner"],
            "default",
        )
        for slug in ("test01", "minimal", "default", "studio_banner"):
            main_window.set_burnin_menu_state(True, slug)
            checked = _checked_slugs(main_window)
            assert checked == [slug], (
                f"after switching to {slug!r} the menu shows {checked} "
                "— exclusivity broken"
            )

    def test_set_state_does_not_emit_template_signal(
        self, main_window: MainWindow,
    ) -> None:
        # The whole point of going via this helper (rather than a
        # plain click) is to NOT re-emit ``burnin_template_requested``
        # — otherwise the App would write the same slug back to prefs
        # and we'd get an infinite ping-pong on boot. Pin it.
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal"],
            "default",
        )
        captured: list[str] = []
        main_window.burnin_template_requested.connect(captured.append)
        main_window.set_burnin_menu_state(True, "test01")
        assert captured == []

    def test_refresh_does_not_emit_template_signal(
        self, main_window: MainWindow,
    ) -> None:
        # Same contract for refresh — populating the menu mustn't be
        # mistaken for a user pick.
        captured: list[str] = []
        main_window.burnin_template_requested.connect(captured.append)
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal"],
            "test01",
        )
        assert captured == []


class TestUserClickAfterSilentInit:
    """The killer scenario, reported in the field:

    Player just opened → ``_silently_check_burnin_slug`` is called
    at boot to tick the previously-saved active template. Then the
    user opens the View menu and clicks a different preset. The
    OLD preset stays ticked alongside the new one — until they
    click the old one a second time.

    Root cause was the helper blocking signals around
    ``setChecked``, which hid the ``changed`` signal from
    QActionGroup. The group's internal ``current`` pointer never
    learned about the silently-checked action, so when the user
    later clicked another preset the group "had no current to
    uncheck" and just added a second tick.

    These tests pin the fix from the angle that actually matters
    (the user-click path), in addition to the lower-level
    exclusivity contract in :class:`TestExclusivity`.
    """

    def test_user_click_after_silent_init_unticks_old(
        self, main_window: MainWindow, qtbot,
    ) -> None:
        # Boot-style init: refresh + silent state sync (mirrors what
        # ``_wire_burnins`` does in App).
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal", "studio_banner"],
            "default",
        )
        main_window.set_burnin_menu_state(True, "default")
        assert _checked_slugs(main_window) == ["default"]

        # Now simulate a user click on a DIFFERENT preset. Use
        # ``trigger()`` which is the closest programmatic equivalent
        # to a real menu click (fires ``triggered`` + ``toggled`` +
        # ``changed`` and toggles the checked state through the same
        # Qt code path as the menu).
        captured: list[str] = []
        main_window.burnin_template_requested.connect(captured.append)
        target = main_window._burnin_template_actions["test01"]
        target.trigger()

        # Exclusivity must have unchecked dailies_default.
        assert _checked_slugs(main_window) == ["test01"], (
            "after a real user click, dailies_default should be "
            "unchecked by QActionGroup. The silent helper at boot "
            "blocked ``changed`` from reaching the group, leaving "
            "``current`` stale, so the click failed to uncheck the "
            "old row."
        )
        # And the click DOES fire the user-pick signal — that's
        # the whole point of distinguishing click vs sync.
        assert captured == ["test01"]

    def test_user_click_sequence_keeps_single_check(
        self, main_window: MainWindow,
    ) -> None:
        # Boot init, then a sequence of clicks. Each click leaves
        # exactly one row ticked — the just-clicked one.
        main_window.refresh_burnin_template_menu(
            ["test01", "default", "minimal", "studio_banner"],
            "default",
        )
        main_window.set_burnin_menu_state(True, "default")
        for slug in ("minimal", "studio_banner", "test01", "default"):
            main_window._burnin_template_actions[slug].trigger()
            assert _checked_slugs(main_window) == [slug], (
                f"after clicking {slug!r} the menu shows "
                f"{_checked_slugs(main_window)} — exclusivity broken"
            )


class TestShowBurninsToggleStillSyncs:
    def test_set_state_enables_checkbox(
        self, main_window: MainWindow,
    ) -> None:
        # The "Show burnins" checkable action and the template
        # submenu live in the same View menu; the helper must keep
        # syncing both.
        main_window.set_burnin_menu_state(True, "default")
        assert main_window._show_burnins_act.isChecked() is True

    def test_set_state_disables_checkbox(
        self, main_window: MainWindow,
    ) -> None:
        main_window.set_burnin_menu_state(True, "default")
        main_window.set_burnin_menu_state(False, "default")
        assert main_window._show_burnins_act.isChecked() is False
