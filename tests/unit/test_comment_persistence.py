"""Tests for :mod:`img_player.comment.persistence` (schema v2).

The comments share the sidecar JSON with the annotations — these
tests focus on the contract that **save_comments doesn't clobber
annotations** and vice versa, plus the v1 → v2 backward-compat
fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

from img_player.annotate.persistence import (
    save_annotations,
    sidecar_path,
)
from img_player.annotate.store import AnnotationStore
from img_player.annotate.stroke import Stroke
from img_player.comment.persistence import load_comments, save_comments
from img_player.comment.store import CommentStore


def _stroke() -> Stroke:
    return Stroke(points=((0.0, 0.0), (1.0, 1.0)), color="#FF0000", size=5.0)


def _stocked_comment_store(layer_id: str, frame: int, text: str) -> CommentStore:
    store = CommentStore()
    store.set_current_layer_id(layer_id)
    store.add_comment(frame, text)
    return store


def _stocked_anno_store(layer_id: str, frame: int) -> AnnotationStore:
    store = AnnotationStore()
    store.set_current_layer_id(layer_id)
    store.add_stroke(frame, _stroke())
    return store


# ============================================================================
# Round-trip
# ============================================================================


class TestRoundTrip:
    def test_save_then_load_preserves_comments(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = CommentStore()
        store.set_current_layer_id("layer-A")
        c1 = store.add_comment(42, "first comment")
        c2 = store.add_comment(42, "second comment")
        store.add_comment(87, "elsewhere")

        assert save_comments(
            path, store, layer_id="layer-A", name_hint="render",
        ) is True
        loaded = load_comments(
            path, layer_id="layer-A", name_hint="render",
        )
        assert loaded is not None
        assert loaded.commented_frames() == frozenset({42, 87})
        assert loaded.comments_at(42) == (c1, c2)

    def test_atomic_save_no_tmp_left(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = _stocked_comment_store("layer-A", 42, "hello")
        save_comments(
            path, store, layer_id="layer-A", name_hint="render",
        )
        assert not path.with_suffix(path.suffix + ".tmp").exists()


# ============================================================================
# Cohabitation with annotations
# ============================================================================


class TestCoexistenceWithAnnotations:
    def test_save_comments_does_not_clobber_annotations(
        self, tmp_path: Path,
    ) -> None:
        """The headline contract: writing comments preserves the
        ``"frames"`` (strokes) sub-tree on the same layer entry."""
        path = sidecar_path(tmp_path)

        anno = _stocked_anno_store("layer-A", 10)
        save_annotations(
            path, anno, layer_id="layer-A", name_hint="render",
        )

        com = _stocked_comment_store("layer-A", 20, "hello")
        save_comments(
            path, com, layer_id="layer-A", name_hint="render",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data["layers"]["layer-A"]
        assert "frames" in entry
        assert "10" in entry["frames"]
        assert "comments" in entry
        assert "20" in entry["comments"]

    def test_save_annotations_does_not_clobber_comments(
        self, tmp_path: Path,
    ) -> None:
        """Mirror: writing annotations second preserves comments
        written first."""
        path = sidecar_path(tmp_path)

        com = _stocked_comment_store("layer-A", 20, "hello")
        save_comments(
            path, com, layer_id="layer-A", name_hint="render",
        )

        anno = _stocked_anno_store("layer-A", 10)
        save_annotations(
            path, anno, layer_id="layer-A", name_hint="render",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data["layers"]["layer-A"]
        assert "comments" in entry
        assert "20" in entry["comments"]
        assert "frames" in entry
        assert "10" in entry["frames"]


# ============================================================================
# Layer isolation
# ============================================================================


class TestLayerIsolation:
    def test_two_layers_in_one_file(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store_a = _stocked_comment_store("layer-A", 10, "a-comment")
        save_comments(
            path, store_a, layer_id="layer-A", name_hint="render",
        )
        store_b = _stocked_comment_store("layer-B", 20, "b-comment")
        save_comments(
            path, store_b, layer_id="layer-B", name_hint="playblast",
        )

        loaded_a = load_comments(
            path, layer_id="layer-A", name_hint="render",
        )
        loaded_b = load_comments(
            path, layer_id="layer-B", name_hint="playblast",
        )
        assert loaded_a is not None and loaded_b is not None
        assert loaded_a.commented_frames() == frozenset({10})
        assert loaded_b.commented_frames() == frozenset({20})


# ============================================================================
# Failure modes
# ============================================================================


class TestFailureModes:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_comments(
            tmp_path / "does_not_exist.json",
            layer_id="layer-A", name_hint="render",
        ) is None

    def test_unknown_layer_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = _stocked_comment_store("layer-A", 10, "hello")
        save_comments(
            path, store, layer_id="layer-A", name_hint="render",
        )
        assert load_comments(
            path, layer_id="layer-B", name_hint="other",
        ) is None

    def test_no_comments_key_returns_none(self, tmp_path: Path) -> None:
        """A layer entry with frames but no comments: loading
        comments returns ``None`` so the caller starts empty."""
        path = sidecar_path(tmp_path)
        anno = _stocked_anno_store("layer-A", 10)
        save_annotations(
            path, anno, layer_id="layer-A", name_hint="render",
        )
        assert load_comments(
            path, layer_id="layer-A", name_hint="render",
        ) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.write_text("{ this is broken", encoding="utf-8")
        assert load_comments(
            path, layer_id="layer-A", name_hint="render",
        ) is None


# ============================================================================
# v1 backward-compat
# ============================================================================


class TestV1BackwardCompat:
    def test_loads_v1_comments_via_name_hint(self, tmp_path: Path) -> None:
        """Pre-v2 sidecars stored comments under
        ``sequences[<basename>].comments``. The v2 loader falls back
        to the legacy basename via ``name_hint``."""
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sequences": {
                        "render": {
                            "comments": {
                                "42": [
                                    {
                                        "id": "a",
                                        "text": "hello",
                                        "author": "alice",
                                        "created_at": "2026-04-27T18:00:00+00:00",
                                        "updated_at": "2026-04-27T18:00:00+00:00",
                                    },
                                ],
                            },
                        },
                    },
                },
            ),
            encoding="utf-8",
        )
        loaded = load_comments(
            path, layer_id="any-new-uuid", name_hint="render",
        )
        assert loaded is not None
        assert loaded.commented_frames() == frozenset({42})
        assert loaded.comments_at(42)[0].text == "hello"
