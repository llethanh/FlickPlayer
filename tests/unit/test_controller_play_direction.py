"""Tests for PlayerController.play_direction — the start / flip / pause
state machine that wires the forward/reverse play buttons.

This is the logic the user's bug report exposed: clicking "play forward"
while playing in reverse used to drop into pause; clicking "play forward"
twice didn't pause; etc. We pin every cell of that 3×2 grid here so it
can't regress.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from img_player.cache.frame_cache import FrameCache
from img_player.player.controller import PlayerController


@pytest.fixture
def controller(qtbot) -> PlayerController:  # type: ignore[no-untyped-def]
    cache = MagicMock(spec=FrameCache)
    return PlayerController(cache)


@pytest.fixture
def loaded_controller(controller: PlayerController) -> PlayerController:
    """A controller with a fake sequence attached so play() doesn't
    early-return on `sequence is None`."""
    seq = MagicMock()
    seq.first_frame = 1
    seq.last_frame = 100
    seq.frames = []
    seq.frame_count = 100
    controller._sequence = seq  # noqa: SLF001
    # Reset the dropped_frames counter that load_sequence would normally
    # clear; the rest of the state is fine at its dataclass defaults.
    return controller


# --------------------------------------------------------------------- Paused → play

class TestFromPaused:
    def test_play_forward_from_paused_starts_forward(
        self, loaded_controller: PlayerController,
    ) -> None:
        assert not loaded_controller.state.is_playing
        loaded_controller.play_direction(1)
        assert loaded_controller.state.is_playing
        assert loaded_controller.state.direction == 1

    def test_play_reverse_from_paused_starts_reverse(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(-1)
        assert loaded_controller.state.is_playing
        assert loaded_controller.state.direction == -1


# --------------------------------------------------------------------- Same direction → pause

class TestSameDirectionPauses:
    def test_play_forward_while_playing_forward_pauses(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(1)
        loaded_controller.play_direction(1)
        assert not loaded_controller.state.is_playing
        # Direction stays at +1 — paused, not flipped.
        assert loaded_controller.state.direction == 1

    def test_play_reverse_while_playing_reverse_pauses(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(-1)
        loaded_controller.play_direction(-1)
        assert not loaded_controller.state.is_playing
        assert loaded_controller.state.direction == -1


# --------------------------------------------------------------------- Other direction → flip

class TestOtherDirectionFlips:
    def test_play_reverse_while_playing_forward_flips_to_reverse(
        self, loaded_controller: PlayerController,
    ) -> None:
        # This was the headline bug: clicking reverse while playing
        # forward used to pause instead of flipping.
        loaded_controller.play_direction(1)
        assert loaded_controller.state.is_playing
        loaded_controller.play_direction(-1)
        assert loaded_controller.state.is_playing  # still playing!
        assert loaded_controller.state.direction == -1

    def test_play_forward_while_playing_reverse_flips_to_forward(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(-1)
        loaded_controller.play_direction(1)
        assert loaded_controller.state.is_playing
        assert loaded_controller.state.direction == 1


# --------------------------------------------------------------------- Sign normalization

class TestDirectionNormalization:
    def test_zero_is_treated_as_forward(self, loaded_controller: PlayerController) -> None:
        # Defensive: a 0 sneaking through (e.g. from a misconfigured
        # signal source) should not crash. We map it to forward.
        loaded_controller.play_direction(0)
        assert loaded_controller.state.direction == 1

    def test_arbitrary_positive_maps_to_one(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(42)
        assert loaded_controller.state.direction == 1

    def test_arbitrary_negative_maps_to_minus_one(
        self, loaded_controller: PlayerController,
    ) -> None:
        loaded_controller.play_direction(-42)
        assert loaded_controller.state.direction == -1
