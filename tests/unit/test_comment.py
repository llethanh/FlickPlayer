"""Tests for :class:`img_player.comment.comment.Comment`.

The Comment is a frozen dataclass with auto-filled metadata
(uuid id, timestamps, OS author). Tests cover construction,
immutability, the edited() copy, and JSON round-trip.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from img_player.comment.comment import Comment


def _tick() -> None:
    """Sleep just enough to guarantee ``_now()`` returns a strictly
    later ISO timestamp on the next call.

    ``datetime.now()`` has microsecond precision, but two
    back-to-back calls in the same Python statement can return the
    same value on fast hardware. The product code never relies on
    micro-second-level monotonicity (a human-typed comment edit
    takes orders of magnitude longer), but the tests need a way to
    force a difference deterministically.
    """
    time.sleep(0.002)


# ============================================================================
# Construction via Comment.new()
# ============================================================================


class TestNew:
    def test_new_fills_all_metadata(self) -> None:
        c = Comment.new("hello", author="alice")
        assert c.text == "hello"
        assert c.author == "alice"
        assert c.id  # uuid hex, non-empty
        assert c.created_at  # ISO-8601 string
        assert c.updated_at == c.created_at  # fresh: equal

    def test_new_generates_unique_ids(self) -> None:
        a = Comment.new("a", author="x")
        b = Comment.new("b", author="x")
        assert a.id != b.id

    def test_new_uses_os_user_when_no_author(self) -> None:
        """The default author is the OS-detected username — at least
        a non-empty string. We don't pin the exact value (it varies
        per machine / CI bot)."""
        c = Comment.new("hello")
        assert isinstance(c.author, str)
        assert c.author  # non-empty

    def test_new_timestamp_is_utc_iso(self) -> None:
        """``created_at`` parses cleanly as a timezone-aware datetime
        in UTC — round-trips through ``datetime.fromisoformat``."""
        c = Comment.new("hello", author="alice")
        dt = datetime.fromisoformat(c.created_at)
        assert dt.tzinfo is not None
        # Within ~5 seconds of "now" — sanity, not a tight bound.
        delta = abs(
            (datetime.now(timezone.utc) - dt).total_seconds()
        )
        assert delta < 5.0


# ============================================================================
# Immutability
# ============================================================================


class TestImmutability:
    def test_frozen(self) -> None:
        c = Comment.new("hello", author="alice")
        with pytest.raises((AttributeError, TypeError)):
            c.text = "boom"  # type: ignore[misc]


# ============================================================================
# .edited()
# ============================================================================


class TestEdited:
    def test_edited_replaces_text(self) -> None:
        original = Comment.new("first", author="alice")
        revised = original.edited("second")
        assert revised.text == "second"

    def test_edited_preserves_id_and_author_and_created_at(self) -> None:
        """Identity is the id; the author and original creation time
        belong to the comment forever — only updated_at moves."""
        original = Comment.new("first", author="alice")
        _tick()  # ensure updated_at is strictly later than created_at
        revised = original.edited("second")
        assert revised.id == original.id
        assert revised.author == original.author
        assert revised.created_at == original.created_at
        assert revised.updated_at != original.updated_at

    def test_is_edited_flag(self) -> None:
        original = Comment.new("first", author="alice")
        assert original.is_edited is False
        _tick()
        revised = original.edited("second")
        assert revised.is_edited is True


# ============================================================================
# JSON round-trip
# ============================================================================


class TestJsonRoundTrip:
    def test_to_dict_and_back(self) -> None:
        original = Comment.new("hello", author="alice")
        out = Comment.from_dict(original.to_dict())
        assert out == original

    def test_from_dict_missing_field_raises(self) -> None:
        """The persistence layer catches the exception; the
        dataclass itself is strict."""
        with pytest.raises(KeyError):
            Comment.from_dict({"id": "x", "text": "y", "author": "a"})
