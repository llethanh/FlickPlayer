"""Unit tests for burnin token substitution.

Pin every supported token + the edge cases (empty values, unknown
tokens, escape syntax, single-pass guarantee). These are pure
functions so the tests are fast and don't need a Qt fixture.
"""

from __future__ import annotations

import datetime
import os

import pytest

from img_player.burnins.tokens import (
    RenderContext,
    resolve,
    supported_tokens,
)


def _ctx(**overrides) -> RenderContext:  # type: ignore[no-untyped-def]
    """Build a RenderContext from kwargs — keeps each test focused on
    the field it pins instead of fighting boilerplate defaults."""
    return RenderContext(**overrides)


# ---------------------------------------------------------------------- Per-token

class TestFrameTokens:
    def test_frame_substitutes(self) -> None:
        assert resolve("{frame}", _ctx(frame=1042)) == "1042"

    def test_frame_total_substitutes(self) -> None:
        assert resolve("{frame_total}", _ctx(frame_total=1244)) == "1244"

    def test_frame_pair(self) -> None:
        out = resolve(
            "frame {frame}/{frame_total}",
            _ctx(frame=1042, frame_total=1244),
        )
        assert out == "frame 1042/1244"

    def test_missing_frame_renders_empty_keeping_surround(self) -> None:
        # The cue that data is missing is the gap, not a crash.
        out = resolve("frame {frame}/{frame_total}", _ctx(frame=1042))
        assert out == "frame 1042/"


class TestTimecode:
    def test_timecode_25fps(self) -> None:
        # 25 fps × 60 s = 1500 frames → 00:01:00:00.
        assert resolve("{timecode}", _ctx(frame=1500, fps=25.0)) == "00:01:00:00"

    def test_timecode_24fps_with_frame_remainder(self) -> None:
        # 24 fps → 1 second = 24 frames; 100 = 00:00:04:04.
        assert resolve("{timecode}", _ctx(frame=100, fps=24.0)) == "00:00:04:04"

    def test_timecode_zero(self) -> None:
        assert resolve("{timecode}", _ctx(frame=0, fps=24.0)) == "00:00:00:00"

    def test_timecode_without_fps_is_empty(self) -> None:
        assert resolve("{timecode}", _ctx(frame=120)) == ""

    def test_timecode_without_frame_is_empty(self) -> None:
        assert resolve("{timecode}", _ctx(fps=24.0)) == ""

    def test_timecode_negative_fps_is_empty(self) -> None:
        # Defensive: -1 fps is nonsense; don't divide by it.
        assert resolve("{timecode}", _ctx(frame=10, fps=-1.0)) == ""


class TestFps:
    def test_integer_fps_renders_integer(self) -> None:
        assert resolve("{fps}", _ctx(fps=24.0)) == "24"

    def test_fractional_fps_trims_trailing_zeros(self) -> None:
        assert resolve("{fps}", _ctx(fps=23.976)) == "23.976"

    def test_missing_fps_empty(self) -> None:
        assert resolve("{fps}", _ctx()) == ""


class TestResolution:
    def test_resolution_uses_x_separator(self) -> None:
        assert resolve("{resolution}", _ctx(width=1920, height=1080)) == "1920x1080"

    def test_resolution_missing_height(self) -> None:
        assert resolve("{resolution}", _ctx(width=1920)) == ""

    def test_resolution_zero_dimension(self) -> None:
        assert resolve("{resolution}", _ctx(width=0, height=1080)) == ""


class TestStrings:
    def test_sequence(self) -> None:
        out = resolve(
            "{sequence}",
            _ctx(sequence="SH0010_Rendered_RGB.####.exr"),
        )
        assert out == "SH0010_Rendered_RGB.####.exr"

    def test_layer_name(self) -> None:
        assert resolve("{layer_name}", _ctx(layer_name="plate")) == "plate"

    def test_session_name(self) -> None:
        assert resolve("{session_name}", _ctx(session_name="dailies.session")) == "dailies.session"

    def test_empty_strings_render_blank(self) -> None:
        # Default ctx → every string field is "". No crash, no
        # ``None`` leaked into the output.
        assert resolve("[{sequence}]", _ctx()) == "[]"


class TestDateUser:
    def test_explicit_date(self) -> None:
        assert resolve("{date}", _ctx(date="2026-05-27")) == "2026-05-27"

    def test_explicit_user(self) -> None:
        assert resolve("{user}", _ctx(user="reviewer42")) == "reviewer42"

    def test_default_date_is_today(self) -> None:
        # Default RenderContext has date=None → resolver fills in the
        # local date. We pin the format (ISO YYYY-MM-DD) and that it's
        # within ±1 day of today (covers a midnight roll-over).
        out = resolve("{date}", _ctx())
        # ISO date can be parsed back.
        d = datetime.date.fromisoformat(out)
        today = datetime.date.today()
        assert abs((today - d).days) <= 1

    def test_default_user_from_env(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("USERNAME", "reviewer-windows")
        monkeypatch.delenv("USER", raising=False)
        assert resolve("{user}", _ctx()) == "reviewer-windows"


# ---------------------------------------------------------------------- Robustness

class TestUnknownTokens:
    def test_unknown_token_stays_literal(self) -> None:
        # Editor preview shows the typo verbatim — easier to spot.
        out = resolve("hello {whatever}", _ctx())
        assert out == "hello {whatever}"

    def test_mixed_known_and_unknown(self) -> None:
        out = resolve(
            "{frame}/{frame_total} ({mystery})",
            _ctx(frame=1, frame_total=10),
        )
        assert out == "1/10 ({mystery})"

    def test_empty_string_is_empty(self) -> None:
        assert resolve("", _ctx()) == ""

    def test_text_without_any_token_is_returned_verbatim(self) -> None:
        out = resolve("just plain text", _ctx())
        assert out == "just plain text"


class TestEscapes:
    def test_doubled_braces_are_literal(self) -> None:
        # ``{{`` → ``{``, ``}}`` → ``}``. Same convention as
        # :py:meth:`str.format`.
        assert resolve("{{not a token}}", _ctx()) == "{not a token}"

    def test_doubled_braces_mixed_with_token(self) -> None:
        out = resolve(
            "{{frame}} = {frame}",
            _ctx(frame=42),
        )
        assert out == "{frame} = 42"


class TestSinglePassGuarantee:
    """A token's value can contain literal ``{otherstuff}`` and it
    must NOT trigger another pass. Prevents accidental recursion if
    a user types something like ``layer_name = "{layer_name}"``."""

    def test_token_value_containing_brace_is_not_recursed(self) -> None:
        # User pasted a fake placeholder into the sequence name.
        ctx = _ctx(
            sequence="{frame_total}",   # value contains a brace
            frame_total=999,
        )
        # First pass substitutes {sequence} → "{frame_total}". A
        # broken recursive resolver would then expand that to "999".
        # Our single-pass resolver leaves it alone.
        assert resolve("{sequence}", ctx) == "{frame_total}"


class TestLayerFrameTokens:
    """``{layer_frame}`` / ``{layer_frame_total}`` are the source-
    frame numbering the user sees on disk for the topmost-visible
    layer. They're distinct from ``{frame}`` (master timeline);
    most templates show BOTH so a reviewer can quote either number
    depending on whether they're talking to the timeline-aware
    director or the disk-aware compositor."""

    def test_layer_frame_substitutes(self) -> None:
        assert resolve("{layer_frame}", _ctx(layer_frame=220)) == "220"

    def test_layer_frame_total_substitutes(self) -> None:
        assert (
            resolve("{layer_frame_total}", _ctx(layer_frame_total=350))
            == "350"
        )

    def test_layer_frame_pair_format(self) -> None:
        out = resolve(
            "layer {layer_frame}/{layer_frame_total}",
            _ctx(layer_frame=220, layer_frame_total=350),
        )
        assert out == "layer 220/350"

    def test_missing_layer_frame_renders_empty(self) -> None:
        # No layer covers this master frame → both tokens collapse to
        # empty strings; the surround makes the gap visible.
        out = resolve(
            "layer {layer_frame}/{layer_frame_total}",
            _ctx(),
        )
        assert out == "layer /"

    def test_layer_frame_is_independent_of_master_frame(self) -> None:
        # Master vs layer are different fields — pin that the
        # master-frame value doesn't leak into the layer slot.
        out = resolve(
            "{frame} vs {layer_frame}",
            _ctx(frame=1042, layer_frame=220),
        )
        assert out == "1042 vs 220"


class TestSupportedTokens:
    def test_supported_tokens_covers_every_substituted_name(self) -> None:
        # The editor's dropdown reads ``supported_tokens()``; if a new
        # token gets added in the resolver but not in this list, the
        # editor stops surfacing it. Pin the equivalence.
        supported = set(supported_tokens())
        # Render every token name in a probe string; supported tokens
        # consume it, unsupported stays literal. Use distinct values
        # so we can identify by output.
        ctx = _ctx(
            frame=1, frame_total=2, fps=24.0,
            width=10, height=20,
            sequence="seqval", layer_name="layval",
            session_name="sessval",
            date="2026-01-01", user="usr",
        )
        for name in supported:
            assert resolve(f"<{{{name}}}>", ctx) != f"<{{{name}}}>", (
                f"supported token {name!r} did not get substituted"
            )
