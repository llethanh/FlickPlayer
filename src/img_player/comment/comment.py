"""The :class:`Comment` — a textual note attached to a specific frame.

Sister to :class:`~img_player.annotate.stroke.Stroke`, which carries
the visual side of the same per-frame review notion. Where strokes
are polylines in image-space, comments are plain text — what a
director writes when handing notes back to an animator.

Design:

* **UUID id.** Stable across edits (the on-disk row keeps the same
  id even when the text changes), so the panel UI can address a
  comment unambiguously when the user clicks edit / delete.
* **Author.** Auto-populated via :func:`getpass.getuser` at
  creation. No login flow — solo VFX is the primary use case;
  multi-user shared sidecars get an organic attribution.
* **Two timestamps.** ``created_at`` is the original moment;
  ``updated_at`` advances when the user edits. The panel displays
  ``created_at`` and tags edited entries with ``(modifié à HH:MM)``
  in the Slack / Kitsu convention. No edit history is kept — the
  store overwrites the text in place.
* **Frozen dataclass.** Same reasoning as ``Stroke``: immutability
  protects any future undo stack from silent corruption, and makes
  the type hashable.
"""

from __future__ import annotations

import getpass
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

log = logging.getLogger(__name__)


def _new_id() -> str:
    """Stable identifier for a comment row.

    UUID4 — random, no clock dependency. Hex-only string keeps it
    JSON-friendly and human-readable enough for log lines.
    """
    return uuid.uuid4().hex


def _now() -> str:
    """Timezone-aware ISO-8601 UTC timestamp.

    ISO with offset ('+00:00') round-trips through ``datetime``
    without ambiguity. UTC keeps cross-machine notes consistent —
    the panel formats local time for display, but the persisted
    value is always universal.
    """
    return datetime.now(UTC).isoformat()


def _detect_author() -> str:
    """The OS username that owns this session.

    Best-effort: if the environment doesn't yield a user name (rare
    on locked-down CI bots), fall back to ``"anonymous"`` rather
    than raising. A comment with author=anonymous is still a
    valid comment — much better than crashing the panel.
    """
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover — defensive
        log.warning("[comment] could not detect OS user; defaulting to 'anonymous'")
        return "anonymous"


@dataclass(frozen=True)
class Comment:
    """A single textual note attached to a frame.

    Construct via :meth:`new` for a fresh comment (id, timestamps,
    author auto-filled). Call :meth:`edited` to derive a new
    instance with replaced text and an advanced ``updated_at``.
    """

    id: str
    text: str
    author: str
    created_at: str
    updated_at: str

    @classmethod
    def new(cls, text: str, *, author: str | None = None) -> Comment:
        """Build a fresh comment with auto-filled metadata.

        ``author`` overrides the OS-detected username — useful for
        tests that need a deterministic field, or for any future
        flow that injects a logged-in user.
        """
        now = _now()
        return cls(
            id=_new_id(),
            text=text,
            author=author if author is not None else _detect_author(),
            created_at=now,
            updated_at=now,
        )

    def edited(self, new_text: str) -> Comment:
        """Return a new Comment with replaced text and a fresh
        ``updated_at`` — same id, same author, same created_at."""
        return Comment(
            id=self.id,
            text=new_text,
            author=self.author,
            created_at=self.created_at,
            updated_at=_now(),
        )

    @property
    def is_edited(self) -> bool:
        """True if the comment has been modified since creation."""
        return self.updated_at != self.created_at

    def to_dict(self) -> dict[str, str]:
        """JSON-friendly representation. Inverse of :meth:`from_dict`."""
        return {
            "id": self.id,
            "text": self.text,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Comment:
        """Build from a JSON dict. Raises ``KeyError`` / ``TypeError``
        on a malformed payload — callers wrap in try/except and skip
        the bad row (the persistence layer does this)."""
        return cls(
            id=str(data["id"]),
            text=str(data["text"]),
            author=str(data["author"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )
