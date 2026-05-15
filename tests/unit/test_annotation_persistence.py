"""Tests for :mod:`img_player.annotate.persistence` (schema v2).

Sidecar JSON: atomic save, schema versioning, graceful failures,
per-layer isolation, v1 → v2 backward-compat load + auto-migrate.
"""

from __future__ import annotations

import json
from pathlib import Path

from img_player.annotate.persistence import (
    SCHEMA_VERSION,
    SIDECAR_FILENAME,
    load_annotations,
    save_annotations,
    sidecar_path,
)
from img_player.annotate.store import AnnotationStore
from img_player.annotate.stroke import Stroke


def _stroke(color: str = "#FF0000", size: float = 5.0) -> Stroke:
    return Stroke(points=((0.0, 0.0), (10.0, 10.0)), color=color, size=size)


def _stocked_store(layer_id: str, frame: int, *strokes: Stroke) -> AnnotationStore:
    """Construct a store with ``layer_id`` as current and ``strokes``
    on ``frame``. Helper for the round-trip tests."""
    store = AnnotationStore()
    store.set_current_layer_id(layer_id)
    for s in strokes:
        store.add_stroke(frame, s)
    return store


# ============================================================================
# sidecar_path
# ============================================================================


class TestSidecarPath:
    def test_sidecar_filename_is_dot_prefixed(self) -> None:
        """Hidden on Linux/macOS, less visible on Windows."""
        assert SIDECAR_FILENAME.startswith(".")

    def test_path_lives_in_sequence_dir(self, tmp_path: Path) -> None:
        assert sidecar_path(tmp_path) == tmp_path / SIDECAR_FILENAME


# ============================================================================
# Save (v2 round-trip + atomicity)
# ============================================================================


class TestSave:
    def test_save_creates_file_with_v2_payload(self, tmp_path: Path) -> None:
        store = _stocked_store("layer-A", 42, _stroke())
        path = sidecar_path(tmp_path)
        assert save_annotations(
            path, store,
            layer_id="layer-A",
            name_hint="render",
            source_path_hint=str(tmp_path),
        ) is True
        assert path.exists()

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == SCHEMA_VERSION == 2
        assert "saved_at" in data
        assert "img_player_version" in data
        assert "layer-A" in data["layers"]
        # Hints are populated for future portability.
        assert data["layers"]["layer-A"]["name_hint"] == "render"
        assert data["layers"]["layer-A"]["source_path_hint"] == str(tmp_path)

    def test_save_is_atomic(self, tmp_path: Path) -> None:
        """Successful save leaves no .tmp file behind."""
        store = _stocked_store("layer-A", 42, _stroke())
        path = sidecar_path(tmp_path)
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )
        assert path.exists()
        assert not (tmp_path / (SIDECAR_FILENAME + ".tmp")).exists()

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        store = _stocked_store("layer-A", 42, _stroke())
        path = tmp_path / "nested" / "deep" / SIDECAR_FILENAME
        assert save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        ) is True
        assert path.exists()

    def test_save_preserves_other_layers_in_existing_file(
        self, tmp_path: Path,
    ) -> None:
        """Two layers cohabit in one sidecar. Saving layer A must not
        clobber layer B's payload."""
        path = sidecar_path(tmp_path)
        store_a = _stocked_store("layer-A", 10, _stroke(color="#FF0000"))
        save_annotations(
            path, store_a, layer_id="layer-A", name_hint="render",
        )

        store_b = _stocked_store("layer-B", 20, _stroke(color="#00FF00"))
        save_annotations(
            path, store_b, layer_id="layer-B", name_hint="playblast",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data["layers"].keys()) == {"layer-A", "layer-B"}

    def test_save_overwrites_same_layer(self, tmp_path: Path) -> None:
        """Re-saving the same layer replaces its previous payload —
        no append. Otherwise removed strokes resurrect across saves."""
        path = sidecar_path(tmp_path)
        store = _stocked_store("layer-A", 10, _stroke(color="#FF0000"))
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )

        store.remove_stroke(10, 0)
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        # Empty layer entries are pruned at save — so layer-A drops
        # out of the file entirely.
        assert "layer-A" not in data.get("layers", {})

    def test_save_treats_corrupt_existing_as_empty(self, tmp_path: Path) -> None:
        """If the existing sidecar is unreadable JSON, the save
        proceeds and overwrites it."""
        path = sidecar_path(tmp_path)
        path.write_text("{ this is not json", encoding="utf-8")

        store = _stocked_store("layer-A", 10, _stroke())
        assert save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        ) is True

        data = json.loads(path.read_text(encoding="utf-8"))
        assert "layer-A" in data["layers"]


# ============================================================================
# Load (v2)
# ============================================================================


class TestLoad:
    def test_round_trip_preserves_strokes(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        s1 = _stroke(color="#FF0000")
        s2 = _stroke(color="#00FF00")
        store = AnnotationStore()
        store.set_current_layer_id("layer-A")
        store.add_stroke(42, s1)
        store.add_stroke(42, s2)
        store.add_stroke(87, _stroke(color="#0000FF"))
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )

        loaded = load_annotations(
            path, layer_id="layer-A", name_hint="render",
        )
        assert loaded is not None
        assert loaded.annotated_frames() == frozenset({42, 87})
        assert loaded.strokes_at(42) == (s1, s2)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = load_annotations(
            tmp_path / "does_not_exist.json",
            layer_id="layer-A", name_hint="render",
        )
        assert result is None

    def test_unknown_layer_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        store = _stocked_store("layer-A", 42, _stroke())
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )
        # File has layer-A; we ask for layer-B with no name_hint
        # match either. Should be no-op.
        assert load_annotations(
            path, layer_id="layer-B", name_hint="other",
        ) is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.write_text("{ this is not json", encoding="utf-8")
        assert load_annotations(
            path, layer_id="layer-A", name_hint="render",
        ) is None

    def test_layer_isolation(self, tmp_path: Path) -> None:
        """Loading layer A returns A's strokes only — B's strokes in
        the same file don't leak."""
        path = sidecar_path(tmp_path)
        store_a = _stocked_store("layer-A", 10, _stroke(color="#FF0000"))
        save_annotations(
            path, store_a, layer_id="layer-A", name_hint="render",
        )
        store_b = _stocked_store("layer-B", 20, _stroke(color="#00FF00"))
        save_annotations(
            path, store_b, layer_id="layer-B", name_hint="playblast",
        )

        loaded_a = load_annotations(
            path, layer_id="layer-A", name_hint="render",
        )
        loaded_b = load_annotations(
            path, layer_id="layer-B", name_hint="playblast",
        )
        assert loaded_a is not None and loaded_b is not None
        assert loaded_a.annotated_frames() == frozenset({10})
        assert loaded_b.annotated_frames() == frozenset({20})

    def test_load_skips_malformed_stroke_inside_valid_file(
        self, tmp_path: Path,
    ) -> None:
        """A single broken stroke in an otherwise-valid file is
        dropped, the rest loads."""
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "saved_at": "2026-05-15T00:00:00+00:00",
                    "img_player_version": "1.5.15",
                    "layers": {
                        "layer-A": {
                            "name_hint": "render",
                            "source_path_hint": "",
                            "frames": {
                                "42": [
                                    {
                                        "color": "#FF0000",
                                        "size": 5.0,
                                        "points": [[0, 0], [1, 1]],
                                    },
                                    {
                                        # Bad: invalid color.
                                        "color": "not-a-hex",
                                        "size": 5.0,
                                        "points": [[0, 0]],
                                    },
                                ],
                            },
                            "comments": {},
                        },
                    },
                },
            ),
            encoding="utf-8",
        )
        loaded = load_annotations(
            path, layer_id="layer-A", name_hint="render",
        )
        assert loaded is not None
        assert len(loaded.strokes_at(42)) == 1


# ============================================================================
# v2 name-hint fallback (layer rebuilt with new uuid)
# ============================================================================


class TestNameHintFallback:
    def test_loads_v2_entry_by_name_hint_when_layer_id_mismatches(
        self, tmp_path: Path,
    ) -> None:
        """User opens the source via "Open Recent" rather than a
        saved session → fresh uuid for the layer. The v2 file still
        has the previous uuid as a key, but the ``name_hint``
        matches the new layer's basename. Loader picks the right
        entry via the hint."""
        path = sidecar_path(tmp_path)
        # Save under an "old" uuid.
        store = _stocked_store("old-uuid", 42, _stroke())
        save_annotations(
            path, store, layer_id="old-uuid", name_hint="render",
        )

        # Reload under a "new" uuid + same name_hint.
        loaded = load_annotations(
            path, layer_id="new-uuid", name_hint="render",
        )
        assert loaded is not None
        assert loaded.annotated_frames() == frozenset({42})


# ============================================================================
# v1 → v2 backward-compat
# ============================================================================


class TestV1BackwardCompat:
    def test_loads_v1_file_via_name_hint(self, tmp_path: Path) -> None:
        """Pre-v2 sidecars used ``sequences[<basename>]`` keys. The
        v2 loader falls back to ``sequences[name_hint]`` so existing
        files keep loading without migration friction."""
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "saved_at": "2026-04-27T00:00:00+00:00",
                    "img_player_version": "1.5.10",
                    "sequences": {
                        "render": {
                            "frames": {
                                "42": [
                                    {
                                        "color": "#FF0000",
                                        "size": 5.0,
                                        "points": [[0, 0], [1, 1]],
                                    },
                                ],
                            },
                        },
                    },
                },
            ),
            encoding="utf-8",
        )
        loaded = load_annotations(
            path, layer_id="any-new-uuid", name_hint="render",
        )
        assert loaded is not None
        assert loaded.annotated_frames() == frozenset({42})

    def test_first_save_migrates_v1_to_v2_on_disk(self, tmp_path: Path) -> None:
        """After loading a v1 file and saving, the on-disk shape
        flips to v2: schema_version=2, ``layers`` instead of
        ``sequences``."""
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sequences": {
                        "render": {
                            "frames": {
                                "42": [
                                    {
                                        "color": "#FF0000",
                                        "size": 5.0,
                                        "points": [[0, 0]],
                                    },
                                ],
                            },
                        },
                    },
                },
            ),
            encoding="utf-8",
        )

        store = _stocked_store("layer-A", 87, _stroke(color="#00FF00"))
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 2
        # The legacy "render" basename was the one we just saved
        # under "layer-A", so it's NOT re-keyed under a synthetic
        # id — it's superseded by the live save.
        assert "layer-A" in data["layers"]
        assert "legacy:render" not in data.get("layers", {})

    def test_unrelated_v1_basenames_migrate_to_synthetic_ids(
        self, tmp_path: Path,
    ) -> None:
        """A v1 file with multiple basenames: the one we're saving
        replaces its slot, but the OTHERS get re-keyed under
        ``legacy:<basename>`` so their data stays reachable."""
        path = sidecar_path(tmp_path)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sequences": {
                        "render": {
                            "frames": {
                                "10": [
                                    {
                                        "color": "#FF0000",
                                        "size": 5.0,
                                        "points": [[0, 0]],
                                    },
                                ],
                            },
                        },
                        "playblast": {
                            "frames": {
                                "20": [
                                    {
                                        "color": "#00FF00",
                                        "size": 5.0,
                                        "points": [[0, 0]],
                                    },
                                ],
                            },
                        },
                    },
                },
            ),
            encoding="utf-8",
        )

        store = _stocked_store("layer-A", 42, _stroke())
        save_annotations(
            path, store, layer_id="layer-A", name_hint="render",
        )

        data = json.loads(path.read_text(encoding="utf-8"))
        # "render" is now under layer-A.
        assert "layer-A" in data["layers"]
        # "playblast" was orphaned by the migration → synthetic id.
        assert "legacy:playblast" in data["layers"]
        # Re-loading playblast via name_hint match still works.
        loaded = load_annotations(
            path, layer_id="some-future-uuid", name_hint="playblast",
        )
        assert loaded is not None
        assert loaded.annotated_frames() == frozenset({20})
