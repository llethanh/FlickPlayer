"""Smoke + behaviour tests for the burnin editor dialog.

Headless via pytest-qt. The editor is mostly Qt UI plumbing — we
pin the few non-UI behaviours that matter:

* The dialog constructs without crashing.
* Adding / removing elements mutates the working template.
* "Save As" writes a user template the storage layer can re-load.
* "Set as active" emits the slug the App is supposed to consume.
"""

from __future__ import annotations

import pytest

from img_player.burnins.builtins import builtin_template
from img_player.burnins.model import TextElement
from img_player.burnins.storage import is_user_template, save_user_template
from img_player.ui.burnin_editor import BurninEditorDialog


@pytest.fixture(autouse=True)
def _user_dir(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "img_player.burnins.storage.user_burnins_dir",
        lambda: tmp_path / "burnins",
    )
    return tmp_path / "burnins"


@pytest.fixture
def editor(qtbot) -> BurninEditorDialog:  # type: ignore[no-untyped-def]
    d = BurninEditorDialog()
    qtbot.addWidget(d)
    return d


class TestConstruction:
    def test_dialog_constructs(self, editor: BurninEditorDialog) -> None:
        # Pin the basic facts: combo populated, default preset
        # selected by default. v1.7 ships exactly one builtin.
        assert editor._template_combo.count() >= 1
        assert editor._current_slug == "default"

    def test_initial_template_is_default(
        self, editor: BurninEditorDialog,
    ) -> None:
        # We don't pin the .name field exactly — the builtin name is
        # human-readable, slug is the key.
        assert editor._template.top_bar.enabled is True
        assert editor._template.bottom_bar.enabled is True


class TestMutations:
    def test_add_element_grows_top_bar(
        self, editor: BurninEditorDialog,
    ) -> None:
        before = len(editor._template.top_bar.elements)
        editor._add_element("text")
        assert len(editor._template.top_bar.elements) == before + 1
        assert isinstance(editor._template.top_bar.elements[-1], TextElement)

    def test_toggle_top_bar_off(self, editor: BurninEditorDialog) -> None:
        editor._top_bar_enabled.setChecked(False)
        assert editor._template.top_bar.enabled is False

    def test_height_pct_updates_template(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Spinbox is in PERCENT (10.0 == 10 %); template stores 0.10.
        editor._top_bar_height.setValue(10.0)
        assert editor._template.top_bar.height_pct == pytest.approx(0.10)

    def test_height_loaded_as_percent(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Default ``default`` ships with top_bar height_pct =
        # 0.04 → spinbox should display 4.0. (v1.7 trimmed both
        # bars to 4 % to match the screenshot the user dialled in.)
        assert editor._top_bar_height.value() == pytest.approx(4.0)

    def test_color_picker_preserves_opacity(
        self, editor: BurninEditorDialog, monkeypatch,
    ) -> None:
        # The colour picker is RGB-only — it must NOT clobber the
        # bar's alpha (that's the opacity spinbox's job). The default
        # builtin ships with 0.75 alpha → picking a new colour should
        # keep 0.75.
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        monkeypatch.setattr(
            QColorDialog, "getColor",
            staticmethod(lambda *a, **kw: QColor(10, 20, 30)),
        )
        editor._pick_bar_color("top")
        # Default builtin alpha is 0.75 — preserved.
        assert editor._template.top_bar.bg_color == "rgba(10, 20, 30, 0.75)"

    def test_color_picker_cancel_keeps_color(
        self, editor: BurninEditorDialog, monkeypatch,
    ) -> None:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        monkeypatch.setattr(
            QColorDialog, "getColor",
            staticmethod(lambda *a, **kw: QColor()),
        )
        before = editor._template.top_bar.bg_color
        editor._pick_bar_color("top")
        assert editor._template.top_bar.bg_color == before

    def test_opacity_spinbox_edits_only_alpha(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Spinbox is in percent; 50 → 0.50 alpha. RGB stays.
        from img_player.burnins.renderer import _parse_color
        r_before, g_before, b_before, _ = _parse_color(
            editor._template.top_bar.bg_color,
        )
        editor._top_bar_opacity.setValue(50)
        r_after, g_after, b_after, a_after = _parse_color(
            editor._template.top_bar.bg_color,
        )
        assert (r_after, g_after, b_after) == (r_before, g_before, b_before)
        # 50 % of 255 → 128 (rounded).
        assert a_after == 128

    def test_opacity_loaded_from_template(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Default builtin top bar has rgba(245, 168, 48, 0.75)
        # → slider should read 75.
        assert editor._top_bar_opacity.value() == 75


class TestPreviewClickToSelect:
    """Clicking on a rendered element in the preview canvas must
    select the matching tree row so the properties pane loads its
    values (so the user gets the same "edit in place" UX as picking
    from the tree)."""

    def test_preview_click_emits_signal_and_tree_selects(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Programmatically push an element_clicked through the canvas
        # — that's what a real mouse click would do after hit-testing.
        editor._preview.element_clicked.emit("bottom", 0)
        path = editor._current_selection_path()
        assert path == ("elem", "bottom", 0)

    def test_canvas_records_rects_on_render(
        self, editor: BurninEditorDialog,
    ) -> None:
        # The canvas re-renders on every template change. After
        # loading the default (Dailies), it should have populated
        # ``_element_rects`` for hit-testing.
        assert len(editor._preview._element_rects) > 0
        # Both bars should appear.
        bar_ids = {bid for (bid, _) in editor._preview._element_rects}
        assert bar_ids == {"top", "bottom"}


class TestSaveAs:
    def test_save_as_writes_user_template(
        self, editor: BurninEditorDialog, monkeypatch,
    ) -> None:
        # Skip the QInputDialog by stubbing it with a known answer.
        from PySide6.QtWidgets import QInputDialog
        monkeypatch.setattr(
            QInputDialog, "getText",
            lambda *a, **kw: ("My Custom Template", True),
        )
        editor._on_save_as()
        # The new slug is now active.
        assert editor._current_slug == "my_custom_template"
        # And the file exists on disk.
        assert is_user_template("my_custom_template")

    def test_save_as_cancel_keeps_previous_slug(
        self, editor: BurninEditorDialog, monkeypatch,
    ) -> None:
        from PySide6.QtWidgets import QInputDialog
        monkeypatch.setattr(
            QInputDialog, "getText",
            lambda *a, **kw: ("", False),
        )
        editor._on_save_as()
        assert editor._current_slug == "default"
        assert not is_user_template("default")


class TestChangeSignalFlow:
    """The form's ``changed`` signal is zero-arg but it's connected to
    widgets that emit with payloads (text, int…). Without lambda
    adapters, the connection raises
    ``TypeError: changed() only accepts 0 argument(s), 1 given``
    the first time the user edits anything — which is exactly the
    crash a real run hit."""

    def test_text_edit_does_not_raise(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Select the first element of the top bar (a TextElement).
        editor._select_path(("elem", "top", 0))
        # Mutate the text — must NOT raise.
        editor._props._text.setText("new content")
        # Round-trip: the template was updated.
        assert (
            editor._template.top_bar.elements[0].text == "new content"
        )

    def test_font_family_change_does_not_raise(
        self, editor: BurninEditorDialog,
    ) -> None:
        editor._select_path(("elem", "top", 0))
        editor._props._font_family.setCurrentText("Arial")
        # Round-trip.
        assert (
            editor._template.top_bar.elements[0].font_family == "Arial"
        )

    def test_font_family_combo_is_not_editable(
        self, editor: BurninEditorDialog,
    ) -> None:
        # The user asked for a regular dropdown, not editable. Pin it
        # so a future refactor doesn't accidentally re-enable typing.
        assert editor._props._font_family.isEditable() is False

    def test_custom_template_font_is_added_to_combo(
        self, editor: BurninEditorDialog,
    ) -> None:
        # A template referencing a font not in our preset list (e.g.
        # imported from a colleague) should still load — the editor
        # adds the family to the combo so ``setCurrentText`` works.
        from img_player.burnins.model import TextElement
        custom_elem = TextElement(font_family="ComicCustomFont", text="x")
        editor._props.load_element(custom_elem)
        assert (
            editor._props._font_family.findText("ComicCustomFont") >= 0
        )
        assert (
            editor._props._font_family.currentText() == "ComicCustomFont"
        )

    def test_font_size_change_does_not_raise(
        self, editor: BurninEditorDialog,
    ) -> None:
        editor._select_path(("elem", "top", 0))
        editor._props._font_size.setValue(20)
        assert editor._template.top_bar.elements[0].font_size_pt == 20

    def test_anchor_change_does_not_raise(
        self, editor: BurninEditorDialog,
    ) -> None:
        editor._select_path(("elem", "top", 0))
        editor._props._anchor.setCurrentText("right")
        assert editor._template.top_bar.elements[0].anchor == "right"


class TestApplyEmits:
    def test_set_as_active_emits_slug(
        self, editor: BurninEditorDialog, qtbot,
    ) -> None:
        with qtbot.waitSignal(editor.template_applied, timeout=500) as blocker:
            editor._on_apply()
        assert blocker.args == ["default"]


class TestExistingUserTemplate:
    def test_user_template_loads_into_editor(
        self, editor: BurninEditorDialog,
    ) -> None:
        # Save one BEFORE creating the editor would be cleaner but
        # we work with the existing fixture — re-trigger combo
        # population after writing the file.
        save_user_template("preset_alpha", builtin_template("default"))
        editor._refresh_template_combo()
        idx = editor._template_combo.findData("preset_alpha")
        assert idx >= 0
        editor._template_combo.setCurrentIndex(idx)
        # Combo + state updated to user slug.
        assert editor._current_slug == "preset_alpha"
        # Save / Delete buttons now enabled (user template, not
        # builtin).
        assert editor._save_btn.isEnabled()
        assert editor._delete_btn.isEnabled()
