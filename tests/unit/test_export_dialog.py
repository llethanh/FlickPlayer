"""Tests for :class:`ExportDialog` widget state sync (v0.5.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from img_player.export.dialog import ExportDialog
from img_player.export.settings import ExportSettings


@pytest.fixture
def make_dialog(qtbot, tmp_path: Path):  # type: ignore[no-untyped-def]
    def _factory(**overrides) -> ExportDialog:  # type: ignore[no-untyped-def]
        defaults = {
            "in_frame": 1,
            "out_frame": 100,
            "source_in_frame": 1,
            "source_out_frame": 100,
            "source_width": 1920,
            "source_height": 1080,
            "source_fps": 24.0,
            "initial_settings": ExportSettings(
                output_dir=tmp_path,
                in_frame=1, out_frame=100,
                format_key="png",
            ),
        }
        defaults.update(overrides)
        d = ExportDialog(**defaults)
        qtbot.addWidget(d)
        return d
    return _factory


class TestInitialState:
    def test_default_format_is_png(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        s = d.get_settings()
        assert s.format_key == "png"
        assert s.is_image_sequence

    def test_in_out_seeded_from_args(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog(in_frame=10, out_frame=42, initial_settings=None)
        s = d.get_settings()
        assert s.in_frame == 10
        assert s.out_frame == 42

    def test_estimate_label_populated(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        # The label is updated in _refresh_estimate at construction.
        text = d._estimate_label.text()
        assert "Estimated size" in text


class TestFormatSwitch:
    def test_switching_to_video_swaps_dropdown(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        d._radio_video.setChecked(True)
        d._on_kind_changed()
        # First entry should now be a video format key.
        first_key = d._format_combo.itemData(0)
        assert first_key in {"h264_mp4", "h265_mp4"}

    def test_video_format_default_bake_on(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        d._radio_video.setChecked(True)
        d._on_kind_changed()
        assert d._display_xform_chk.isChecked() is True

    def test_exr_format_default_bake_off(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        # Find EXR in the imgseq dropdown.
        for i in range(d._format_combo.count()):
            if d._format_combo.itemData(i) == "exr":
                d._format_combo.setCurrentIndex(i)
                break
        # display_bake_default for EXR is False — checkbox follows.
        assert d._display_xform_chk.isChecked() is False


class TestResolutionPresets:
    def test_source_preset_disables_wh(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        d._res_combo.setCurrentIndex(0)  # Source
        d._on_res_preset(0)
        assert d._width_spin.isEnabled() is False
        assert d._height_spin.isEnabled() is False
        s = d.get_settings()
        assert s.width is None
        assert s.height is None

    def test_custom_preset_enables_wh(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        last = d._res_combo.count() - 1
        d._res_combo.setCurrentIndex(last)  # Custom…
        d._on_res_preset(last)
        assert d._width_spin.isEnabled() is True
        assert d._height_spin.isEnabled() is True

    def test_1080p_preset_sets_dimensions(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        # Find "1080p" preset.
        for i in range(d._res_combo.count()):
            if "1080" in d._res_combo.itemText(i):
                d._res_combo.setCurrentIndex(i)
                d._on_res_preset(i)
                break
        s = d.get_settings()
        assert s.width == 1920
        assert s.height == 1080


class TestRangeButtons:
    def test_use_full_range_resets_inputs(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog(in_frame=20, out_frame=30, source_in_frame=1, source_out_frame=100)
        d._use_full_range()
        assert d._in_spin.value() == 1
        assert d._out_spin.value() == 100


class TestBakeToggle:
    def test_disabling_bake_disables_sidecar(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        d._bake_chk.setChecked(True)
        d._copy_sidecar_chk.setChecked(True)
        d._bake_chk.setChecked(False)
        # The signal-driven slot should have unchecked + disabled
        # the sidecar checkbox.
        assert d._copy_sidecar_chk.isEnabled() is False
        assert d._copy_sidecar_chk.isChecked() is False


class TestAdvancedToggle:
    def test_advanced_panel_starts_hidden(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        assert d._advanced_panel.isVisibleTo(d) is False

    def test_advanced_panel_shows_on_click(self, make_dialog) -> None:  # type: ignore[no-untyped-def]
        d = make_dialog()
        d._advanced_btn.setChecked(True)
        d._toggle_advanced(True)
        assert d._advanced_panel.isVisibleTo(d) is True
