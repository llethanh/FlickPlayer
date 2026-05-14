"""Tests for File → Save Frame As… dialog + handler.

Covers:

* :class:`SaveFrameDialog.settings` builds the right path/format combo
  from the user's input (filename strip + extension swap).
* The resolution-picker UX: Source preset → ``(None, None)``, named
  presets fill the W/H spinboxes, Custom mode + Lock aspect ratio
  mirrors W ↔ H.
* :func:`_write_image` picks the correct Qt format hint per extension.

The full render path (FrameRenderer + OCIO + compare bake) is
exercised indirectly via the multi-frame export tests — we only test
the save-frame-specific UI / IO surface here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from img_player.export.settings import RESOLUTION_PRESETS
from img_player.preferences import _qbool
from img_player.save_frame_handler import (
    _QT_FORMAT_FOR_EXT,
    _opt_pos_int,
    _write_image,
)
from img_player.ui.save_frame_dialog import (
    FORMATS,
    SaveFrameDialog,
    SaveFrameSettings,
)


# Shared fixture: minimum-required constructor args. Each test pulls
# this dict and overrides only what it cares about, so adding a new
# required arg to SaveFrameDialog is a single edit here.
_DIALOG_DEFAULTS = {
    "source_width": 1920,
    "source_height": 1080,
}


# ============================================================================
# SaveFrameDialog.settings — filename / format
# ============================================================================


class TestSaveFrameDialogSettings:
    def test_default_settings_use_suggested_filename(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="render_0042",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        s = dialog.settings()
        assert s.path == tmp_path / "render_0042.png"
        assert s.fmt == "png"
        assert s.with_annotations is True

    def test_format_combo_overrides_typed_extension(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """User types ``shot.jpg`` then picks PNG → output is shot.png."""
        dialog = SaveFrameDialog(
            suggested_filename="render_0042",
            suggested_dir=tmp_path,
            last_format="png",
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        dialog._filename_edit.setText("shot.jpg")
        s = dialog.settings()
        assert s.path.name == "shot.png"

    def test_empty_filename_falls_back_to_suggested(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="render_0042",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        dialog._filename_edit.setText("   ")
        s = dialog.settings()
        assert s.path.name == "render_0042.png"

    def test_last_format_round_trips(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            last_format="jpg",
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        assert dialog.settings().fmt == "jpg"

    def test_unknown_last_format_falls_back_to_default(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            last_format="not_a_format",
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        assert dialog.settings().fmt == "png"

    def test_annotations_toggle_persists(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            last_with_annotations=False,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        s = dialog.settings()
        assert s.with_annotations is False


# ============================================================================
# Resolution picker — Source / preset / Custom + Lock aspect
# ============================================================================


class TestResolutionPicker:
    def test_source_preset_returns_none_dims(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """The Source preset is the dialog's "keep input size" sentinel
        — settings.width and settings.height both come back as None so
        the handler can branch on a single None check."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=2048,
            source_height=858,
        )
        qtbot.addWidget(dialog)
        # Default selection is the first preset (Source).
        assert dialog._res_combo.currentIndex() == 0
        s = dialog.settings()
        assert s.width is None
        assert s.height is None

    def test_named_preset_returns_preset_dims(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Picking 1080p fills the spinboxes with 1920×1080 and the
        returned settings carry those exact dims."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        # Find the 1080p preset index by label.
        idx = next(
            i for i, (label, _w, _h) in enumerate(RESOLUTION_PRESETS)
            if "1080p" in label
        )
        dialog._res_combo.setCurrentIndex(idx)
        s = dialog.settings()
        assert s.width == 1920
        assert s.height == 1080

    def test_custom_preset_uses_spin_values(
        self, qtbot, tmp_path: Path,
    ) -> None:
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        custom_idx = len(RESOLUTION_PRESETS) - 1
        dialog._res_combo.setCurrentIndex(custom_idx)
        dialog._width_spin.setValue(1234)
        dialog._height_spin.setValue(720)
        s = dialog.settings()
        assert s.width == 1234
        assert s.height == 720

    def test_lock_aspect_mirrors_width_to_height(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """In Custom mode with Lock aspect ON, editing W rewrites H
        to preserve the captured ratio."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,  # ratio 16:9
        )
        qtbot.addWidget(dialog)
        custom_idx = len(RESOLUTION_PRESETS) - 1
        dialog._res_combo.setCurrentIndex(custom_idx)
        # Pre-fill with a known ratio then lock aspect.
        dialog._width_spin.setValue(1920)
        dialog._height_spin.setValue(1080)
        dialog._lock_aspect_chk.setChecked(True)
        # Halving W should halve H.
        dialog._width_spin.setValue(960)
        assert dialog._height_spin.value() == 540

    def test_lock_aspect_disabled_outside_custom(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Lock aspect only makes sense in Custom mode — disabled on
        Source / named presets so its state can't influence them."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        # Default = Source preset → lock disabled.
        assert not dialog._lock_aspect_chk.isEnabled()
        # Switch to Custom → enabled.
        custom_idx = len(RESOLUTION_PRESETS) - 1
        dialog._res_combo.setCurrentIndex(custom_idx)
        assert dialog._lock_aspect_chk.isEnabled()

    def test_last_dims_round_trip_custom(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Stored prefs with dims that don't match a named preset
        restore the Custom mode + the exact W/H values."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,
            last_width=2570,
            last_height=1090,  # arbitrary non-preset dims
        )
        qtbot.addWidget(dialog)
        custom_idx = len(RESOLUTION_PRESETS) - 1
        assert dialog._res_combo.currentIndex() == custom_idx
        assert dialog._width_spin.value() == 2570
        assert dialog._height_spin.value() == 1090

    def test_last_dims_round_trip_named_preset(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Stored prefs matching a named preset's dims restore that
        preset (not Custom)."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,
            last_width=1280,
            last_height=720,  # 720p preset
        )
        qtbot.addWidget(dialog)
        idx = next(
            i for i, (label, _w, _h) in enumerate(RESOLUTION_PRESETS)
            if "720p" in label
        )
        assert dialog._res_combo.currentIndex() == idx


# ============================================================================
# Contact-sheet preset — injected at the top of the combo when CS is active
# ============================================================================


class TestContactSheetPreset:
    def test_cs_preset_absent_when_not_passed(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Without ``contact_sheet_size`` the combo holds only the
        export's RESOLUTION_PRESETS — no Contact sheet entry."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            **_DIALOG_DEFAULTS,
        )
        qtbot.addWidget(dialog)
        labels = [
            dialog._res_combo.itemText(i)
            for i in range(dialog._res_combo.count())
        ]
        assert not any(label.startswith("Contact sheet") for label in labels)
        # Source still sits at index 0 in the regular case.
        assert dialog._source_idx == 0

    def test_cs_preset_prepended_and_default(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """When CS mode is active, the dialog injects a "Contact sheet
        (W×H)" entry AT INDEX 0 and selects it by default — the user
        gets the current composite size in one click."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,
            contact_sheet_size=(4800, 2700),
        )
        qtbot.addWidget(dialog)
        assert dialog._res_combo.itemText(0).startswith("Contact sheet")
        assert "4800" in dialog._res_combo.itemText(0)
        assert "2700" in dialog._res_combo.itemText(0)
        # Default selection is the CS preset, not Source.
        assert dialog._res_combo.currentIndex() == 0
        # Source shifted by one because CS preset is at idx 0.
        assert dialog._source_idx == 1
        # settings() returns the CS preset's exact dims (not None).
        s = dialog.settings()
        assert s.width == 4800
        assert s.height == 2700

    def test_cs_preset_with_zero_dims_is_ignored(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """Zero / negative CS dims (= caller couldn't resolve the size)
        are dropped silently — falls back to the regular preset list."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,
            contact_sheet_size=(0, 1080),
        )
        qtbot.addWidget(dialog)
        labels = [
            dialog._res_combo.itemText(i)
            for i in range(dialog._res_combo.count())
        ]
        assert not any(label.startswith("Contact sheet") for label in labels)

    def test_cs_preset_overrides_stored_last_dims(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """The CS preset's "this is the snapshot the user wants right
        now" semantics outrank a previously-stored Custom W×H — the
        prior pick belongs to a different mode."""
        dialog = SaveFrameDialog(
            suggested_filename="x",
            suggested_dir=tmp_path,
            source_width=1920,
            source_height=1080,
            last_width=999,
            last_height=555,  # arbitrary stored dims
            contact_sheet_size=(4800, 2700),
        )
        qtbot.addWidget(dialog)
        # Default = CS preset (idx 0), NOT the last_width / last_height
        # round-trip that would land on Custom.
        assert dialog._res_combo.currentIndex() == 0
        s = dialog.settings()
        assert s.width == 4800
        assert s.height == 2700


# ============================================================================
# _write_image
# ============================================================================


class TestWriteImage:
    @pytest.mark.parametrize("ext", ["png", "jpg", "tif", "bmp", "webp"])
    def test_writes_known_format(self, tmp_path: Path, ext: str) -> None:
        img = QImage(8, 8, QImage.Format.Format_RGBA8888)
        img.fill(0xFF112233)
        settings = SaveFrameSettings(
            path=tmp_path / f"out.{ext}",
            fmt=ext,
            with_annotations=False,
            bake_compare=True,
        )
        ok = _write_image(img, settings)
        assert ok is True
        assert settings.path.exists()
        assert settings.path.stat().st_size > 0

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        img = QImage(4, 4, QImage.Format.Format_RGB888)
        img.fill(0xFF000000)
        nested = tmp_path / "a" / "b" / "c"
        settings = SaveFrameSettings(
            path=nested / "x.png",
            fmt="png",
            with_annotations=False,
            bake_compare=True,
        )
        assert _write_image(img, settings)
        assert (nested / "x.png").exists()

    def test_format_map_is_complete_for_dialog_options(self) -> None:
        """Every extension the dialog offers must be in the Qt format
        map — otherwise we'd silently fall back to filename sniffing."""
        for ext, _label in FORMATS:
            assert ext in _QT_FORMAT_FOR_EXT


# ============================================================================
# _opt_pos_int — QSettings nullable-int parser
# ============================================================================


class TestOptPosInt:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("None", None),
            ("none", None),
            ("", None),
            ("0", None),  # non-positive → None (= "Source" fallback)
            ("-5", None),
            ("garbage", None),
            (0, None),
            (-3, None),
            (1920, 1920),
            ("1080", 1080),
        ],
    )
    def test_parsing(self, value: object, expected: int | None) -> None:
        assert _opt_pos_int(value) == expected


# ============================================================================
# _qbool — kept here because the handler reads QSettings via it
# ============================================================================


class TestCoerceBool:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("", False),  # empty string treated as falsy via the lower-set test
            (1, True),
            (0, False),
            (None, "DEFAULT"),  # None falls through
            (object(), "DEFAULT"),  # unknown types fall through
        ],
    )
    def test_coercion(self, value: object, expected: object) -> None:
        if expected == "DEFAULT":
            # Default of True so we can distinguish "fell through" from "False".
            assert _qbool(value, True) is True
            assert _qbool(value, False) is False
        else:
            assert _qbool(value, not bool(expected)) is expected
