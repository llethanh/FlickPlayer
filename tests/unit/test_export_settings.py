"""Tests for :class:`ExportSettings` validation + format catalog (v0.5.0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from img_player.export.settings import (
    AVAILABLE_IMAGE_FORMATS,
    AVAILABLE_VIDEO_FORMATS,
    EXR_COMPRESSIONS,
    PRORES_PROFILES,
    RESOLUTION_PRESETS,
    ExportFormatKind,
    ExportSettings,
    ExportSettingsError,
    estimate_size_bytes,
    format_bytes,
    format_by_key,
)


# ============================================================================
# Catalog sanity
# ============================================================================


class TestCatalog:
    def test_image_formats_have_required_fields(self) -> None:
        keys = {f.key for f in AVAILABLE_IMAGE_FORMATS}
        assert keys == {"png", "jpg", "exr", "tiff"}
        for fmt in AVAILABLE_IMAGE_FORMATS:
            assert fmt.kind == ExportFormatKind.IMAGE_SEQUENCE
            assert fmt.extension.startswith(".")
            assert fmt.codec is None  # video-only field

    def test_video_formats_have_codec_and_pix_fmt(self) -> None:
        for fmt in AVAILABLE_VIDEO_FORMATS:
            assert fmt.kind == ExportFormatKind.VIDEO
            assert fmt.codec
            assert fmt.pix_fmt
            assert fmt.extension.startswith(".")

    def test_format_by_key_lookup(self) -> None:
        assert format_by_key("png").extension == ".png"
        assert format_by_key("h264_mp4").codec == "libx264"
        with pytest.raises(KeyError):
            format_by_key("bogus")

    def test_resolution_presets_in_expected_order(self) -> None:
        labels = [p[0] for p in RESOLUTION_PRESETS]
        assert labels[0] == "Source"
        assert labels[-1] == "Custom…"

    def test_prores_profile_values_unique(self) -> None:
        values = [v for _, v in PRORES_PROFILES]
        assert len(values) == len(set(values))


# ============================================================================
# ExportSettings — defaults + computed properties
# ============================================================================


class TestDefaults:
    def test_defaults_validate(self, tmp_path: Path) -> None:
        s = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=10)
        s.validate()  # no raise

    def test_total_frames_inclusive(self, tmp_path: Path) -> None:
        s = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=10)
        assert s.total_frames == 10

    def test_total_frames_zero_when_inverted(self, tmp_path: Path) -> None:
        s = ExportSettings(output_dir=tmp_path, in_frame=10, out_frame=1)
        assert s.total_frames == 0  # validate() will reject

    def test_is_video_routing(self, tmp_path: Path) -> None:
        png = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=1, format_key="png")
        h264 = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=1, format_key="h264_mp4")
        assert png.is_image_sequence and not png.is_video
        assert h264.is_video and not h264.is_image_sequence


# ============================================================================
# Validation
# ============================================================================


class TestValidation:
    def test_inverted_range_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(output_dir=tmp_path, in_frame=10, out_frame=5)
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_negative_start_frame_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, start_frame=-1,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_zero_width_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, width=0, height=100,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_only_one_dim_set_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, width=1920, height=None,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_jpg_quality_out_of_range_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, jpg_quality=200,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_bad_exr_compression_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, exr_compression="banana",
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_video_crf_out_of_range_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, video_crf=99,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_bad_prores_profile_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, prores_profile=42,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()

    def test_zero_fps_rejected(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=10, fps=-2.0,
        )
        with pytest.raises(ExportSettingsError):
            s.validate()


# ============================================================================
# Round-trip preferences
# ============================================================================


class TestPrefsRoundTrip:
    def test_to_from_prefs_dict(self, tmp_path: Path) -> None:
        s = ExportSettings(
            output_dir=tmp_path,
            in_frame=10,
            out_frame=20,
            format_key="exr",
            width=1920,
            height=1080,
            fps=24.0,
            apply_display_transform=False,
            bake_annotations=False,
            copy_sidecar=True,
            jpg_quality=80,
            exr_compression="piz",
            video_crf=23,
            prores_profile=4,
            h26x_preset="slow",
        )
        d = s.to_prefs_dict()
        s2 = ExportSettings.from_prefs_dict(d, in_frame=10, out_frame=20)
        # Range comes from caller — just sanity check the rest.
        assert s2.format_key == "exr"
        assert s2.width == 1920
        assert s2.height == 1080
        assert s2.fps == 24.0
        assert s2.apply_display_transform is False
        assert s2.bake_annotations is False
        assert s2.copy_sidecar is True
        assert s2.jpg_quality == 80
        assert s2.exr_compression == "piz"
        assert s2.video_crf == 23
        assert s2.prores_profile == 4
        assert s2.h26x_preset == "slow"

    def test_from_prefs_dict_handles_zero_dims_as_none(self, tmp_path: Path) -> None:
        d = {"output_dir": str(tmp_path), "format_key": "png", "width": 0, "height": 0}
        s = ExportSettings.from_prefs_dict(d, in_frame=1, out_frame=10)
        assert s.width is None
        assert s.height is None

    def test_from_prefs_dict_recovers_from_garbage(self, tmp_path: Path) -> None:
        d = {
            "output_dir": str(tmp_path),
            "format_key": "png",
            "width": "banana",
            "fps": "carrot",
            "jpg_quality": "kale",
        }
        # Should not raise — falls back to sane defaults.
        s = ExportSettings.from_prefs_dict(d, in_frame=1, out_frame=10)
        assert s.width is None
        assert s.fps is None
        assert s.jpg_quality == 95


# ============================================================================
# Size estimate
# ============================================================================


class TestEstimate:
    def test_image_estimate_grows_with_frame_count(self, tmp_path: Path) -> None:
        small = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=1)
        big = ExportSettings(output_dir=tmp_path, in_frame=1, out_frame=100)
        assert estimate_size_bytes(big, 1920, 1080, 24.0) > estimate_size_bytes(
            small, 1920, 1080, 24.0
        )

    def test_video_estimate_grows_with_resolution(self, tmp_path: Path) -> None:
        sd = ExportSettings(
            output_dir=tmp_path, in_frame=1, out_frame=24, format_key="h264_mp4",
        )
        # bigger source resolution must give a bigger estimate
        assert estimate_size_bytes(sd, 1920, 1080, 24.0) > estimate_size_bytes(
            sd, 320, 240, 24.0
        )

    def test_format_bytes_units(self) -> None:
        assert format_bytes(0) == "—"
        assert "KB" in format_bytes(2 * 1024)
        assert "MB" in format_bytes(2 * 1024 * 1024)
        assert "GB" in format_bytes(2 * 1024 * 1024 * 1024)

    def test_estimate_zero_when_invalid(self, tmp_path: Path) -> None:
        s = ExportSettings(output_dir=tmp_path, in_frame=10, out_frame=5)  # inverted
        assert estimate_size_bytes(s, 1920, 1080, 24.0) == 0


# ============================================================================
# EXR_COMPRESSIONS / PRORES_PROFILES sanity (cross-check the dialog
# wiring relies on these being non-empty and self-consistent)
# ============================================================================


class TestStaticTables:
    def test_exr_compressions_include_zip(self) -> None:
        assert "zip" in EXR_COMPRESSIONS
        assert "none" in EXR_COMPRESSIONS

    def test_prores_profiles_include_4444(self) -> None:
        labels = [label for label, _ in PRORES_PROFILES]
        values = [v for _, v in PRORES_PROFILES]
        assert "4444" in labels
        assert 4 in values
