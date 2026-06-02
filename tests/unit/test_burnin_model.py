"""Unit tests for the burnin template model + JSON I/O + builtins.

The model is the foundation every other burnin module builds on
(tokens, renderer, editor, export bake) so we pin the contract:

* dataclasses are frozen (snapshots are safe to share across threads);
* the JSON round-trip preserves every field on every element kind;
* unknown ``type`` raises (broken template) but unknown FIELDS are
  dropped silently (forward compat — a newer Flick adds a field, an
  older Flick still loads the template);
* the three shipped builtins parse + round-trip cleanly.
"""

from __future__ import annotations

import json

import pytest

from img_player.burnins.builtins import BUILTINS, builtin_template
from img_player.burnins.model import (
    BurninBar,
    BurninTemplate,
    ImageElement,
    SpacerElement,
    TextElement,
    load_template,
    save_template,
    template_from_dict,
    template_to_dict,
)


# ---------------------------------------------------------------------- Dataclasses

class TestDataclassesAreFrozen:
    """Frozen = safe to share between the GL upload thread and the Qt
    editor without locking. Pin it."""

    def test_text_element_frozen(self) -> None:
        elem = TextElement(text="hello")
        with pytest.raises(Exception):  # FrozenInstanceError
            elem.text = "mutated"  # type: ignore[misc]

    def test_image_element_frozen(self) -> None:
        elem = ImageElement(path="logo.png")
        with pytest.raises(Exception):
            elem.path = "other.png"  # type: ignore[misc]

    def test_bar_frozen(self) -> None:
        bar = BurninBar()
        with pytest.raises(Exception):
            bar.enabled = False  # type: ignore[misc]

    def test_template_frozen(self) -> None:
        tpl = BurninTemplate(name="X")
        with pytest.raises(Exception):
            tpl.name = "Y"  # type: ignore[misc]


class TestDefaults:
    def test_text_defaults_render_as_sequence(self) -> None:
        # The default token is something useful (sequence name) so an
        # empty template with one element actually shows something.
        elem = TextElement()
        assert "{sequence}" in elem.text

    def test_bar_defaults_to_enabled(self) -> None:
        # A blank template still draws something at the top of the
        # image; the user disables explicitly.
        assert BurninBar().enabled is True

    def test_bar_defaults_to_six_percent(self) -> None:
        # 6 % is the height the screenshots are tuned for.
        assert BurninBar().height_pct == 0.06

    def test_template_defaults_to_empty_bars(self) -> None:
        tpl = BurninTemplate(name="X")
        assert tpl.top_bar.elements == ()
        assert tpl.bottom_bar.elements == ()


# ---------------------------------------------------------------------- JSON round-trip

def _full_template() -> BurninTemplate:
    """A template using every element kind + every field set to a
    non-default value, so the round-trip assertions actually catch a
    dropped attribute."""
    return BurninTemplate(
        name="Round-trip",
        description="Every element kind, every field non-default.",
        top_bar=BurninBar(
            enabled=True,
            height_pct=0.075,
            bg_color="rgba(10, 10, 12, 0.9)",
            elements=(
                TextElement(
                    anchor="left",
                    offset_x=20.0,
                    offset_y=2.0,
                    text="{sequence} — {layer_name}",
                    font_family="JetBrains Mono",
                    font_size_pt=13,
                    font_weight="bold",
                    color="#FFCC55",
                ),
                SpacerElement(
                    anchor="left",
                    offset_x=0.0,
                    offset_y=0.0,
                    width_px=24,
                ),
                ImageElement(
                    anchor="right",
                    offset_x=-12.0,
                    offset_y=0.0,
                    path="logo.png",
                    height_pct=0.7,
                ),
            ),
        ),
        bottom_bar=BurninBar(
            enabled=False,
            height_pct=0.04,
            bg_color="rgba(0, 0, 0, 0.5)",
            elements=(
                TextElement(
                    anchor="center",
                    text="{timecode}",
                    font_family="Inter",
                    font_size_pt=10,
                ),
            ),
        ),
    )


class TestJsonRoundTrip:
    def test_dict_round_trip_preserves_everything(self) -> None:
        original = _full_template()
        as_dict = template_to_dict(original)
        restored = template_from_dict(as_dict)
        assert restored == original

    def test_disk_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        original = _full_template()
        path = tmp_path / "round.burnin.json"
        save_template(original, path)
        restored = load_template(path)
        assert restored == original

    def test_save_is_pretty_printed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Hand-editable file — indent + newlines so a user diffing two
        # templates sees meaningful changes, not a one-line blob.
        save_template(_full_template(), tmp_path / "pretty.burnin.json")
        text = (tmp_path / "pretty.burnin.json").read_text("utf-8")
        assert "\n" in text
        assert "  " in text   # two-space indent

    def test_save_is_atomic_on_existing_file(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Overwriting an existing template must end up with valid JSON
        # — no half-written file mid-rename.
        path = tmp_path / "atomic.burnin.json"
        save_template(_full_template(), path)
        # Overwrite with a different template.
        different = BurninTemplate(name="Different")
        save_template(different, path)
        assert load_template(path) == different
        # And no leftover ``.tmp`` next to the final file.
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []


# ---------------------------------------------------------------------- Forward / backward compat

class TestUnknownFields:
    """A template written by a newer Flick (extra fields the current
    one doesn't know about) must load — we drop the unknown fields
    rather than refusing the file. Conversely an unknown element TYPE
    is a structural error — the loader can't guess what to draw."""

    def test_unknown_element_field_dropped(self) -> None:
        # An element from a future version that added a "shadow" field.
        data = {
            "type": "text",
            "anchor": "left",
            "text": "hi",
            "shadow": True,    # ← unknown to current model
        }
        from img_player.burnins.model import _element_from_dict
        elem = _element_from_dict(data)
        assert isinstance(elem, TextElement)
        assert elem.text == "hi"

    def test_unknown_bar_field_dropped(self) -> None:
        data = {
            "enabled": True,
            "height_pct": 0.08,
            "bg_color": "#000",
            "elements": [],
            "border_radius_px": 4,    # ← unknown to current model
        }
        from img_player.burnins.model import _bar_from_dict
        bar = _bar_from_dict(data)
        assert bar.height_pct == 0.08

    def test_unknown_element_type_raises(self) -> None:
        data = {"type": "video", "anchor": "left"}
        with pytest.raises(ValueError, match="Unknown burnin element type"):
            from img_player.burnins.model import _element_from_dict
            _element_from_dict(data)


# ---------------------------------------------------------------------- Builtins

class TestBuiltins:
    def test_only_default_is_shipped(self) -> None:
        # v1.7 dropped the ``minimal`` + ``studio_banner`` presets;
        # only the single ``default`` ships now. Pin the exact set
        # so a regression that brings them back accidentally would
        # be caught (and so the menu doesn't grow surprising
        # entries).
        assert set(BUILTINS.keys()) == {"default"}

    def test_default_loads_via_helper(self) -> None:
        tpl = builtin_template("default")
        assert isinstance(tpl, BurninTemplate)
        assert tpl.name        # not empty

    def test_default_round_trips(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Saving + loading the builtin should yield the same
        # template. Catches accidents like a missing field on the
        # builtin dict.
        tpl = builtin_template("default")
        path = tmp_path / "default.burnin.json"
        save_template(tpl, path)
        assert load_template(path) == tpl

    def test_unknown_builtin_slug_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown builtin"):
            builtin_template("nonexistent")

    def test_default_has_both_bars_enabled(self) -> None:
        # The default preset is meant to show both bars — pin that.
        tpl = builtin_template("default")
        assert tpl.top_bar.enabled is True
        assert tpl.bottom_bar.enabled is True
        assert tpl.top_bar.elements   # at least one element
        assert tpl.bottom_bar.elements

    @pytest.mark.parametrize(
        "legacy_slug",
        ["dailies_default", "minimal", "studio_banner"],
    )
    def test_legacy_slug_resolves_to_default(
        self, legacy_slug: str,
    ) -> None:
        # Pre-1.7 prefs / sessions saved one of these slugs as the
        # active template — they must still load (returning the
        # shipped default) rather than raise so existing users
        # aren't greeted by a blank burnin.
        tpl = builtin_template(legacy_slug)
        assert isinstance(tpl, BurninTemplate)
        # Same content as the default builtin.
        assert tpl == builtin_template("default")


# ---------------------------------------------------------------------- Robustness

class TestLoadRobustness:
    def test_load_missing_file_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(FileNotFoundError):
            load_template(tmp_path / "ghost.burnin.json")

    def test_load_non_object_json_raises(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # A JSON file that's an array (not an object) at the root
        # isn't a valid template; surface the structural error rather
        # than silently building an Untitled empty template.
        path = tmp_path / "array.burnin.json"
        path.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ValueError, match="not a JSON object"):
            load_template(path)

    def test_load_empty_object_yields_defaults(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # An empty object is a valid (if useless) template; loader
        # returns a defaults-everywhere instance rather than crashing.
        path = tmp_path / "empty.burnin.json"
        path.write_text("{}")
        tpl = load_template(path)
        assert tpl.name == "Untitled"
        assert tpl.top_bar.elements == ()
        assert tpl.bottom_bar.elements == ()
