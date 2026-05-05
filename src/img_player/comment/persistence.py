"""Sidecar persistence for per-frame comments.

Comments live in the **same** sidecar JSON as the annotation
strokes (``.img_player_annotations.json`` next to the sequence) —
both are review notes attached to the same frames, and a single
file means a single atomic write at close-time and a single
"hand the dossier to the director" payload.

The schema's per-basename entry now carries two sub-trees::

    {
      "schema_version": 1,
      "sequences": {
        "render": {
          "frames":   {  ... strokes ...   },   # annotations
          "comments": {  ... comments ...  }    # this module
        }
      }
    }

``schema_version`` stays at 1 — the ``"comments"`` key is
additive, older builds that loaded an annotations-only file
just see no comments and continue.

Save / load follow the same merge-on-read pattern as
:mod:`img_player.annotate.persistence`: reading the existing
file before writing means saving comments doesn't clobber
annotations and vice versa.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from img_player import __version__ as IMG_PLAYER_VERSION
from img_player.annotate.persistence import SCHEMA_VERSION
from img_player.comment.store import CommentStore

log = logging.getLogger(__name__)


def save_comments(
    path: Path,
    store: CommentStore,
    *,
    basename: str,
) -> bool:
    """Atomically write the comment store to ``path``.

    Wraps the store's ``to_dict()`` under
    ``sequences[<basename>]["comments"]`` in the on-disk JSON,
    preserving any other basename's entries AND the existing
    ``"frames"`` (annotations) sub-tree at the same basename.

    Returns ``True`` on success, ``False`` on I/O failure (logged
    at WARNING level — never raises user-facing).
    """
    try:
        existing_sequences: dict[str, dict[str, object]] = {}
        if path.exists():
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
                if prev.get("schema_version") == SCHEMA_VERSION:
                    existing_sequences = prev.get("sequences", {}) or {}
            except (json.JSONDecodeError, OSError):
                log.warning(
                    "[comment] existing sidecar at %s is unreadable; "
                    "overwriting (annotations may be lost)",
                    path,
                )

        # Preserve / create the per-basename entry without losing
        # the strokes ("frames") that annotate.persistence may have
        # already populated.
        bucket = existing_sequences.setdefault(basename, {})
        bucket["comments"] = store.to_dict()

        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "img_player_version": IMG_PLAYER_VERSION,
            "sequences": existing_sequences,
        }

        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as err:  # pragma: no cover — best-effort I/O
        log.warning(
            "[comment] save failed at %s: %s. Comments will be lost on "
            "close — likely a read-only directory.",
            path,
            err,
        )
        return False


def load_comments(
    path: Path,
    *,
    basename: str,
) -> CommentStore | None:
    """Return a freshly populated :class:`CommentStore` from ``path``.

    Returns ``None`` for any failure — missing file, malformed
    JSON, unknown schema, basename absent, no comments key. The
    caller starts with an empty store in that case.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        log.warning(
            "[comment] %s is not valid JSON (%s); starting with empty "
            "comments.",
            path,
            err,
        )
        return None

    if data.get("schema_version") != SCHEMA_VERSION:
        return None

    sequences = data.get("sequences", {})
    payload = sequences.get(basename)
    if not isinstance(payload, dict):
        return None

    if "comments" not in payload:
        # Annotations-only file — let the caller distinguish
        # "no comments stored" from "empty comments stored" so
        # the app can skip the load gracefully.
        return None
    comments_dict = payload["comments"]
    if not isinstance(comments_dict, dict):
        return None

    store = CommentStore()
    store.load_from_dict(comments_dict)
    return store
