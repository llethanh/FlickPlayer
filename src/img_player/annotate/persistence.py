"""Sidecar JSON persistence for annotations (schema v2).

A sequence at ``<dir>/<basename>.<frame>.<ext>`` gets a sidecar at
``<dir>/.img_player_annotations.json``.

**Schema v2 (v1.5.15+).** Top-level key ``"layers"`` keyed on
:attr:`Layer.id` instead of the legacy ``"sequences"`` keyed on
``basename``. Each layer entry carries hints (``name_hint``,
``source_path_hint``) so the file stays self-documenting and we
can match a v2 entry to the right layer at load time even when
``Layer.id`` doesn't survive (e.g. user opened the source via the
"Open Recent" menu rather than restoring a saved session)::

    {
      "schema_version": 2,
      "saved_at": "...",
      "img_player_version": "...",
      "layers": {
        "<layer.id (uuid4)>": {
          "name_hint": "render",
          "source_path_hint": "/abs/path/to/source",
          "frames":   { ... strokes ... },     # annotations
          "comments": { ... comments ... }     # written by comment.persistence
        }
      }
    }

**Schema v1 (legacy)** — see git history before May 2026 — used
``"sequences"`` keyed on basename. The loader still understands v1
files: any ``"sequences"`` sub-tree is converted to "legacy"
:class:`SidecarLayerEntry` instances at load time, with
``source_path_hint`` empty. The first save under v2 drops the v1
``"sequences"`` key in favour of the v2 ``"layers"`` mapping —
auto-migration on first write.

Atomic save (``.tmp`` + rename), schema-versioned, best-effort load
(any failure mode returns ``None`` rather than raising). The merge-
on-read pattern preserves data we don't write ourselves (other
layer entries when a single-layer save fires).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from img_player import __version__ as IMG_PLAYER_VERSION
from img_player.annotate.store import AnnotationStore

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
"""Bump when the on-disk shape changes. The loader keeps a v1
fallback so older sidecars still work; a write always emits v2."""

SIDECAR_FILENAME = ".img_player_annotations.json"
"""Filename of the sidecar inside the sequence's directory.

Kept identical across the v1 → v2 migration so existing files just
work — no rename / orphaning concerns. Dot-prefixed so it's hidden
on Linux/macOS and less visible on Windows.
"""


def sidecar_path(sequence_dir: Path) -> Path:
    """Path to the sidecar JSON for the sequence in ``sequence_dir``."""
    return sequence_dir / SIDECAR_FILENAME


# ============================================================================
# Sidecar in-memory model
# ============================================================================


@dataclass
class SidecarLayerEntry:
    """One layer's slice of a v2 sidecar.

    ``frames`` and ``comments`` are kept as raw JSON-shaped dicts
    (frame_str → list of stroke / comment dicts) so this module
    stays Qt-free; the in-memory store layers (annotate / comment)
    own the parsing into typed objects.

    ``name_hint`` snapshots the layer's basename at save-time so a
    v2 file can still be matched to a freshly-rebuilt layer (with a
    new uuid) via the basename — typical when the user opens a
    sequence from "Open Recent" rather than restoring a session
    file. ``source_path_hint`` is the full source path for debug /
    portability (useful when the file gets moved between machines).
    """

    name_hint: str = ""
    source_path_hint: str = ""
    frames: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    comments: dict[str, list[dict[str, object]]] = field(default_factory=dict)


@dataclass
class SidecarV2:
    """Parsed view of a v2 sidecar (or a v1 one upgraded at load time).

    ``layers`` is the v2 ``layers`` sub-tree, keyed on layer.id.
    ``legacy_by_basename`` is the v1 ``sequences`` sub-tree, keyed
    on basename — populated only when the on-disk file was v1, so
    callers can do a basename-fallback match for layers whose id
    isn't in ``layers`` yet.
    """

    layers: dict[str, SidecarLayerEntry] = field(default_factory=dict)
    legacy_by_basename: dict[str, SidecarLayerEntry] = field(default_factory=dict)


# ============================================================================
# Read
# ============================================================================


def _read_sidecar_file(path: Path) -> SidecarV2 | None:
    """Parse the on-disk JSON into a :class:`SidecarV2`. ``None`` on
    any failure (missing file, bad JSON, unknown shape).

    Both v1 and v2 are accepted: a v2 file populates ``layers``, a
    v1 file populates ``legacy_by_basename``. A file that mixes
    both (= a half-migrated state) populates both — caller can
    prefer one over the other.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        log.warning(
            "[annotations] %s is not valid JSON (%s); ignoring.",
            path, err,
        )
        return None

    out = SidecarV2()
    version = data.get("schema_version")

    # v2 layers sub-tree.
    if version == 2:
        layers = data.get("layers", {})
        if isinstance(layers, dict):
            for layer_id, entry_dict in layers.items():
                if not isinstance(entry_dict, dict):
                    continue
                out.layers[str(layer_id)] = SidecarLayerEntry(
                    name_hint=str(entry_dict.get("name_hint", "")),
                    source_path_hint=str(entry_dict.get("source_path_hint", "")),
                    frames=_safe_dict(entry_dict.get("frames")),
                    comments=_safe_dict(entry_dict.get("comments")),
                )

    # v1 sequences sub-tree (legacy fallback). Captured even when
    # the file claims schema_version=2 in case the user hand-edited.
    sequences = data.get("sequences", {})
    if isinstance(sequences, dict):
        for basename, entry_dict in sequences.items():
            if not isinstance(entry_dict, dict):
                continue
            out.legacy_by_basename[str(basename)] = SidecarLayerEntry(
                name_hint=str(basename),
                source_path_hint="",
                frames=_safe_dict(entry_dict.get("frames")),
                comments=_safe_dict(entry_dict.get("comments")),
            )

    if not out.layers and not out.legacy_by_basename:
        # Nothing usable in the file — likely an unknown schema.
        log.info(
            "[annotations] %s has no recognisable layer / sequence "
            "data (schema_version=%r). Ignoring.",
            path, version,
        )
        return None
    return out


def _safe_dict(value: object) -> dict[str, list[dict[str, object]]]:
    """Defensive cast: only accepts dicts of frame-str → list shape."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[dict[str, object]]] = {}
    for k, v in value.items():
        if isinstance(v, list):
            out[str(k)] = v  # validation deferred to Stroke.from_dict
    return out


def load_annotations(
    path: Path,
    *,
    layer_id: str,
    name_hint: str | None = None,
) -> AnnotationStore | None:
    """Return a freshly populated :class:`AnnotationStore` for one layer.

    Lookup precedence:

    1. v2 ``layers[layer_id]`` exact match — the normal path when
       the user reopened a session and the layer kept its uuid.
    2. v2 ``layers[*]`` whose ``name_hint`` matches the supplied
       ``name_hint`` — handles the case where the layer was rebuilt
       with a fresh uuid (e.g. open-recent) but the basename
       still matches.
    3. v1 ``sequences[name_hint]`` — legacy fallback for files
       written by a build that predates v2.

    Returns ``None`` for missing file, unreadable JSON, or no match
    — the caller starts with an empty store.

    The returned store has ``current_layer_id == layer_id`` and the
    matched frames loaded under that id, so the caller doesn't need
    to know which lookup path succeeded.
    """
    sidecar = _read_sidecar_file(path)
    if sidecar is None:
        return None

    entry = sidecar.layers.get(layer_id)
    if entry is None and name_hint:
        # v2 fallback: basename hint match (= layer rebuilt with new
        # uuid since the last save).
        for candidate in sidecar.layers.values():
            if candidate.name_hint == name_hint:
                entry = candidate
                break
    if entry is None and name_hint:
        # v1 fallback.
        entry = sidecar.legacy_by_basename.get(name_hint)
    if entry is None or not entry.frames:
        return None

    store = AnnotationStore()
    store.set_current_layer_id(layer_id)
    store.load_from_dict(entry.frames)
    return store


# ============================================================================
# Write
# ============================================================================


def save_annotations(
    path: Path,
    store: AnnotationStore,
    *,
    layer_id: str,
    name_hint: str,
    source_path_hint: str = "",
) -> bool:
    """Atomically write **one layer's** strokes to ``path``.

    Other layers' data already in the file is preserved (merge-on-
    read). The v1 ``sequences`` sub-tree is auto-migrated to v2 on
    first write: any basename whose name matches the supplied
    ``name_hint`` is re-keyed under ``layer_id``; other basenames
    are kept as-is in a v2 ``layers`` entry with a synthetic id
    (``"legacy:<basename>"``) so they stay reachable on the next
    open even though the file is now schema_version=2. After the
    first save the v1 ``sequences`` key is dropped from the file.

    Returns ``True`` on success, ``False`` on I/O failure (logged
    at WARNING level — never raises user-facing). Reads the existing
    file first so a comment-store save running in parallel doesn't
    clobber annotation data and vice versa.
    """
    return _save_one_layer_subtree(
        path,
        layer_id=layer_id,
        name_hint=name_hint,
        source_path_hint=source_path_hint,
        frames=store.to_dict()["frames"],
        which="frames",
    )


def _save_one_layer_subtree(
    path: Path,
    *,
    layer_id: str,
    name_hint: str,
    source_path_hint: str,
    frames: dict[str, list[dict[str, object]]] | None = None,
    comments: dict[str, list[dict[str, object]]] | None = None,
    which: str,  # "frames" or "comments" — just for log clarity
) -> bool:
    """Shared write path for the annotate + comment modules.

    Reads the existing file (preserving every other layer's entries),
    upserts the (frames OR comments) sub-tree under ``layers[layer_id]``,
    auto-migrates v1 ``sequences`` data inline, atomically writes back.
    """
    try:
        sidecar = _read_sidecar_file(path) or SidecarV2()

        # Auto-migrate v1 → v2 inline. Each legacy basename becomes a
        # v2 layer entry with a synthetic id so its data stays
        # reachable on next open. If the basename matches the
        # ``name_hint`` of THIS save, prefer the live ``layer_id``
        # instead — that's the live layer the user is editing.
        for basename, legacy_entry in sidecar.legacy_by_basename.items():
            if basename == name_hint:
                # The legacy data for THIS basename is being
                # superseded by the live store's contents in this
                # save. Don't synthesise an id for it.
                continue
            synthetic_id = f"legacy:{basename}"
            if synthetic_id in sidecar.layers:
                # Already migrated in a previous save (= we're
                # called twice for the same path). Skip.
                continue
            sidecar.layers[synthetic_id] = legacy_entry

        # Upsert the live layer's entry.
        entry = sidecar.layers.get(layer_id)
        if entry is None:
            entry = SidecarLayerEntry(
                name_hint=name_hint,
                source_path_hint=source_path_hint,
            )
            sidecar.layers[layer_id] = entry
        else:
            # Refresh hints so the file stays current.
            entry.name_hint = name_hint
            if source_path_hint:
                entry.source_path_hint = source_path_hint
        if frames is not None:
            entry.frames = frames
        if comments is not None:
            entry.comments = comments

        # Drop empty entries so the file doesn't accumulate
        # orphaned (no frames AND no comments) layer records.
        sidecar.layers = {
            lid: e for lid, e in sidecar.layers.items()
            if e.frames or e.comments
        }

        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "img_player_version": IMG_PLAYER_VERSION,
            "layers": {
                lid: {
                    "name_hint": e.name_hint,
                    "source_path_hint": e.source_path_hint,
                    "frames": e.frames,
                    "comments": e.comments,
                }
                for lid, e in sidecar.layers.items()
            },
        }

        tmp = path.with_suffix(path.suffix + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as err:  # pragma: no cover — best-effort I/O
        log.warning(
            "[annotations] save (%s) failed at %s: %s. "
            "Data will be lost on close — likely a read-only "
            "directory (Drive Stream offline, USB write-protect).",
            which, path, err,
        )
        return False
