"""Tests for :mod:`img_player.comment.persistence`.

The comments share the sidecar JSON with the annotations — these
tests focus on the contract that **save_comments doesn't clobber
annotations** and vice versa.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from img_player.annotate.persistence import (
    SCHEMA_VERSION,
    save_annotations,
    sidecar_path,
)
from img_player.annotate.store import AnnotationStore
from img_player.annotate.stroke import Stroke
from img_player.comment.persistence import load_comments, save_comments
from img_player.comment.store import CommentStore


def _stroke() -> Stroke:
    return Stroke(points=((0.0, 0.0), (1.0, 1.0)), color="#FF0000", size=5.0)


# ============================================================================
# Round-trip
# ============================================================================


class TestRoundTrip:
    def test_save_then_load_preserves_comments(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = CommentStore()
        c1 = store.add_comment(42, "first comment")
        c2 = store.add_comment(42, "second comment")
        store.add_comment(87, "elsewhere")

        assert save_comments(path, store, basename="render") is True
        loaded = load_comments(path, basename="render")
        assert loaded is not None
        assert loaded.commented_frames() == frozenset({42, 87})
        assert loaded.comments_at(42) == (c1, c2)

    def test_atomic_save_no_tmp_left(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = CommentStore()
        store.add_comment(42, "hello")
        save_comments(path, store, basename="render")
        assert not path.with_suffix(path.suffix + ".tmp").exists()


# ============================================================================
# Cohabitation with annotations
# ============================================================================


class TestCoexistenceWithAnnotations:
    def test_save_comments_does_not_clobber_annotations(
        self, tmp_path: Path
    ) -> None:
        """The headline contract: writing comments preserves the
        ``"frames"`` (strokes) sub-tree at the same basename. Without
        this guarantee, the second writer could erase the first
        writer's data."""
        path = sidecar_path(tmp_path)

        # Annotations first.
        anno = AnnotationStore()
        anno.add_stroke(10, _stroke())
        save_annotations(path, anno, basename="render")

        # Comments next — saving should NOT lose the strokes.
        com = CommentStore()
        com.add_comment(20, "hello")
        save_comments(path, com, basename="render")

        data = json.loads(path.read_text(encoding="utf-8"))
        bucket = data["sequences"]["render"]
        assert "frames" in bucket  # annotations preserved
        assert "10" in bucket["frames"]
        assert "comments" in bucket
        assert "20" in bucket["comments"]

    def test_save_annotations_does_not_clobber_comments(
        self, tmp_path: Path
    ) -> None:
        """Mirror of the previous test: writing annotations second
        should preserve the comments written first."""
        path = sidecar_path(tmp_path)

        com = CommentStore()
        com.add_comment(20, "hello")
        save_comments(path, com, basename="render")

        anno = AnnotationStore()
        anno.add_stroke(10, _stroke())
        save_annotations(path, anno, basename="render")

        data = json.loads(path.read_text(encoding="utf-8"))
        bucket = data["sequences"]["render"]
        assert "comments" in bucket  # comments preserved
        assert "20" in bucket["comments"]
        assert "frames" in bucket
        assert "10" in bucket["frames"]


# ============================================================================
# Basename isolation
# ============================================================================


class TestBasenameIsolation:
    def test_two_basenames_in_one_file(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)

        store_a = CommentStore()
        store_a.add_comment(10, "a-comment")
        save_comments(path, store_a, basename="render")

        store_b = CommentStore()
        store_b.add_comment(20, "b-comment")
        save_comments(path, store_b, basename="playblast")

        loaded_a = load_comments(path, basename="render")
        loaded_b = load_comments(path, basename="playblast")
        assert loaded_a is not None and loaded_b is not None
        assert loaded_a.commented_frames() == frozenset({10})
        assert loaded_b.commented_frames() == frozenset({20})


# ============================================================================
# Failure modes
# ============================================================================


class TestFailureModes:
    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_comments(
            tmp_path / "does_not_exist.json", basename="render"
        ) is None

    def test_unknown_basename_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = CommentStore()
        store.add_comment(10, "hello")
        save_comments(path, store, basename="render")

        assert load_comments(path, basename="other") is None

    def test_no_comments_key_returns_none(self, tmp_path: Path) -> None:
        """An annotations-only file: the ``"comments"`` key is
        absent. Loading comments should return None gracefully so
        the app starts with an empty store rather than crashing."""
        path = sidecar_path(tmp_path)
        anno = AnnotationStore()
        anno.add_stroke(10, _stroke())
        save_annotations(path, anno, basename="render")

        assert load_comments(path, basename="render") is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.write_text("{ this is broken", encoding="utf-8")
        assert load_comments(path, basename="render") is None

    def test_unknown_schema_version_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "sequences": {"render": {"comments": {}}},
                }
            ),
            encoding="utf-8",
        )
        assert load_comments(path, basename="render") is None
