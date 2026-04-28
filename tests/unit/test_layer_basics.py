"""Tests for :class:`Layer` time arithmetic — pure dataclass, no Qt."""

from __future__ import annotations

from pathlib import Path

import pytest

from img_player.layers import Layer
from img_player.sequence.models import FrameInfo, SequenceInfo


# ============================================================================
# Helpers
# ============================================================================


def _seq(first: int = 1001, last: int = 1100) -> SequenceInfo:
    """Cheap synthetic SequenceInfo with `last - first + 1` frames.

    The frame paths are bogus — Layer arithmetic doesn't open files.
    """
    frames = tuple(
        FrameInfo(path=Path(f"/fake/frame.{n:04d}.exr"), frame_number=n)
        for n in range(first, last + 1)
    )
    return SequenceInfo(
        base_name="frame", extension=".exr", directory=Path("/fake"),
        padding=4, frames=frames,
    )


# ============================================================================
# Default trim from sequence
# ============================================================================


class TestFromSequence:
    def test_defaults_to_full_range(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100))
        assert layer.layer_in == 1001
        assert layer.layer_out == 1100
        assert layer.offset == 0
        assert layer.visible is True
        assert layer.trim_length == 100

    def test_offset_is_applied(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100), offset=50)
        assert layer.offset == 50
        assert layer.master_start == 50
        assert layer.master_end == 149  # offset + trim_length - 1

    def test_id_is_unique_per_layer(self) -> None:
        a = Layer.from_sequence(_seq())
        b = Layer.from_sequence(_seq())
        assert a.id != b.id

    def test_default_name_is_display_pattern(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100))
        # Sequence display pattern is something like "frame.####.exr".
        # We don't assert the exact format — just that it's non-empty
        # and reflects the source.
        assert layer.name
        assert "exr" in layer.name


# ============================================================================
# Time-arithmetic
# ============================================================================


class TestCovers:
    def test_inside_range(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100), offset=50)
        # offset 50, length 100 → master 50..149 inclusive
        assert layer.covers(50)
        assert layer.covers(100)
        assert layer.covers(149)

    def test_outside_range(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100), offset=50)
        assert not layer.covers(49)
        assert not layer.covers(150)
        assert not layer.covers(-1)

    def test_with_trim(self) -> None:
        # Layer covers source 1010..1090 (= 81 frames) at offset 0.
        layer = Layer(
            sequence=_seq(1001, 1100),
            layer_in=1010, layer_out=1090, offset=0,
        )
        assert layer.trim_length == 81
        assert layer.master_start == 0
        assert layer.master_end == 80
        assert layer.covers(0)
        assert layer.covers(80)
        assert not layer.covers(81)


class TestSourceFrameAt:
    def test_offset_zero_no_trim(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100))
        # At master 0 we get the layer's first source frame (1001).
        assert layer.source_frame_at(0) == 1001
        assert layer.source_frame_at(99) == 1100

    def test_with_offset(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100), offset=50)
        # At master 50 → first source frame.
        assert layer.source_frame_at(50) == 1001
        assert layer.source_frame_at(75) == 1026

    def test_with_trim(self) -> None:
        layer = Layer(
            sequence=_seq(1001, 1100),
            layer_in=1010, layer_out=1090, offset=100,
        )
        # At master 100 → first trimmed source frame (1010).
        assert layer.source_frame_at(100) == 1010
        # At master 180 → last trimmed source frame (1090).
        assert layer.source_frame_at(180) == 1090

    def test_negative_offset_allowed(self) -> None:
        # An offset of -50 means the layer's source-frame-1001 lands
        # at master frame -50. Useful when stitching two layers and
        # the "alignment frame" is interior.
        layer = Layer.from_sequence(_seq(1001, 1100), offset=-50)
        assert layer.master_start == -50
        assert layer.covers(-50)
        assert layer.source_frame_at(-50) == 1001


# ============================================================================
# Trim validation
# ============================================================================


class TestTrimValidation:
    def test_default_trim_is_valid(self) -> None:
        layer = Layer.from_sequence(_seq(1001, 1100))
        assert layer.is_trim_valid()

    def test_inverted_trim_is_invalid(self) -> None:
        # User dragged the OUT handle past the IN handle mid-edit —
        # the model accepts the inconsistent state without raising
        # so the UI doesn't have to special-case spinbox input.
        layer = Layer(
            sequence=_seq(1001, 1100),
            layer_in=1080, layer_out=1010, offset=0,
        )
        assert not layer.is_trim_valid()

    def test_trim_outside_source_is_invalid(self) -> None:
        layer = Layer(
            sequence=_seq(1001, 1100),
            layer_in=900, layer_out=1100, offset=0,
        )
        assert not layer.is_trim_valid()
