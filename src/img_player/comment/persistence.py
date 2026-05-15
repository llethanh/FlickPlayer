"""Sidecar persistence for per-frame comments (schema v2).

Comments live in the **same** sidecar JSON as annotation strokes
(``.img_player_annotations.json`` next to the sequence). Schema v2
(v1.5.15+) keys every per-layer entry on :attr:`Layer.id` so a
source-swap on a layer preserves its comments — see the module
docstring of :mod:`img_player.annotate.persistence` for the full
v2 schema description.

Save / load follow the same merge-on-read pattern as
:mod:`img_player.annotate.persistence`: reading the existing file
before writing means saving comments doesn't clobber annotations
and vice versa. Both modules reuse
:func:`img_player.annotate.persistence._save_one_layer_subtree` so
the merge logic lives in exactly one place.
"""

from __future__ import annotations

import logging
from pathlib import Path

from img_player.annotate.persistence import (
    _read_sidecar_file,
    _save_one_layer_subtree,
)
from img_player.comment.store import CommentStore

log = logging.getLogger(__name__)


def save_comments(
    path: Path,
    store: CommentStore,
    *,
    layer_id: str,
    name_hint: str,
    source_path_hint: str = "",
) -> bool:
    """Atomically write **one layer's** comments to ``path``.

    Symmetric to :func:`img_player.annotate.persistence.save_annotations`
    — same v2 layer entry, just the ``"comments"`` sub-tree instead
    of the ``"frames"`` one. Reads the existing file first so the
    annotation save (which wrote ``"frames"``) survives.

    Returns ``True`` on success, ``False`` on I/O failure (logged
    at WARNING level — never raises user-facing).
    """
    return _save_one_layer_subtree(
        path,
        layer_id=layer_id,
        name_hint=name_hint,
        source_path_hint=source_path_hint,
        comments=store.to_dict(),
        which="comments",
    )


def load_comments(
    path: Path,
    *,
    layer_id: str,
    name_hint: str | None = None,
) -> CommentStore | None:
    """Return a freshly populated :class:`CommentStore` for one layer.

    Mirrors :func:`img_player.annotate.persistence.load_annotations`'s
    lookup precedence: v2 ``layers[layer_id]`` → v2 name_hint match →
    v1 ``sequences[name_hint]``. Returns ``None`` when no match has
    a non-empty ``comments`` sub-tree — caller starts with an empty
    store.
    """
    sidecar = _read_sidecar_file(path)
    if sidecar is None:
        return None

    entry = sidecar.layers.get(layer_id)
    if entry is None and name_hint:
        for candidate in sidecar.layers.values():
            if candidate.name_hint == name_hint:
                entry = candidate
                break
    if entry is None and name_hint:
        entry = sidecar.legacy_by_basename.get(name_hint)
    if entry is None or not entry.comments:
        return None

    store = CommentStore()
    store.set_current_layer_id(layer_id)
    store.load_from_dict(entry.comments)
    return store
