"""Tests for the burnin storage layer (user templates on disk +
slug resolution).

Pin the contract the editor + the App preferences rely on:

* Slugify normalises arbitrary names.
* User templates take precedence over builtins under the same slug.
* Delete removes user files but leaves the builtin fallback intact.
* Forward-compat fields drop on round-trip (already covered in the
  model tests but re-checked here through the storage layer).
"""

from __future__ import annotations

import pytest

from img_player.burnins import builtins as _builtins
from img_player.burnins.builtins import builtin_template
from img_player.burnins.model import BurninTemplate
from img_player.burnins.storage import (
    BUILTIN_SLUGS,
    delete_shared_template,
    delete_user_template,
    is_shared_template,
    is_user_template,
    list_all_slugs,
    list_shared_templates,
    list_user_templates,
    save_shared_template,
    save_user_template,
    set_shared_dir_provider,
    shared_burnins_dir,
    slugify,
    template_for_slug,
)


@pytest.fixture(autouse=True)
def _user_dir(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Redirect ``user_burnins_dir`` to a per-test temp folder so the
    test suite doesn't touch the real ``%APPDATA%/FlickPlayer/burnins``
    and so each test starts with a blank slate."""
    monkeypatch.setattr(
        "img_player.burnins.storage.user_burnins_dir",
        lambda: tmp_path / "burnins",
    )
    return tmp_path / "burnins"


@pytest.fixture
def _shared_dir(tmp_path):  # type: ignore[no-untyped-def]
    """Configure storage's shared-dir provider to point at a per-test
    ``shared/`` folder. Cleaned up automatically — the provider is
    reset to ``None`` after the test so global state doesn't leak."""
    shared = tmp_path / "shared"
    shared.mkdir()
    set_shared_dir_provider(lambda: str(shared))
    try:
        yield shared
    finally:
        set_shared_dir_provider(None)


# ---------------------------------------------------------------------- Slugify

class TestSlugify:
    def test_lowercase(self) -> None:
        assert slugify("MyTemplate") == "mytemplate"

    def test_spaces_become_underscores(self) -> None:
        assert slugify("Studio dailies v2") == "studio_dailies_v2"

    def test_strips_punctuation(self) -> None:
        assert slugify("Studio dailies, v2!") == "studio_dailies_v2"

    def test_collapses_runs(self) -> None:
        assert slugify("foo   bar") == "foo_bar"

    def test_empty_falls_back(self) -> None:
        assert slugify("") == "untitled"
        assert slugify("!!!") == "untitled"

    def test_dashes_allowed(self) -> None:
        assert slugify("foo-bar") == "foo-bar"


# ---------------------------------------------------------------------- Listing

class TestListing:
    def test_empty_when_no_user_templates(self, _user_dir) -> None:
        assert list_user_templates() == []
        # Builtins still show up in the combined list.
        assert set(list_all_slugs()) == set(BUILTIN_SLUGS)

    def test_user_templates_show_first(self, _user_dir) -> None:
        save_user_template("my_template", builtin_template("default"))
        save_user_template("another", builtin_template("default"))
        # User-list is sorted by slug.
        assert [slug for slug, _ in list_user_templates()] == [
            "another", "my_template",
        ]
        # Combined list: user first, then builtins.
        combined = list_all_slugs()
        assert combined[:2] == ["another", "my_template"]
        assert set(combined[2:]) == set(BUILTIN_SLUGS)

    def test_shadowing_a_builtin_keeps_it_unique(self, _user_dir) -> None:
        # Saving a user template under a builtin's slug shouldn't list
        # the slug twice — the user's version wins.
        save_user_template("default", builtin_template("default"))
        combined = list_all_slugs()
        assert combined.count("default") == 1


# ---------------------------------------------------------------------- Lookup

class TestLookup:
    def test_builtin_resolves(self, _user_dir) -> None:
        tpl = template_for_slug("default")
        assert isinstance(tpl, BurninTemplate)
        assert tpl.top_bar.enabled is True

    def test_unknown_slug_raises(self, _user_dir) -> None:
        with pytest.raises(KeyError, match="No burnin template"):
            template_for_slug("nonexistent_slug")

    def test_user_template_wins_over_builtin(self, _user_dir) -> None:
        # Build a user template under "default" with a distinctive
        # marker and verify it's what we get back — not the builtin.
        custom = BurninTemplate(name="custom-marker-name")
        save_user_template("default", custom)
        out = template_for_slug("default")
        assert out.name == "custom-marker-name"

    def test_legacy_slug_falls_back_to_default_builtin(
        self, _user_dir,
    ) -> None:
        # Pre-1.7 slugs (``dailies_default`` etc.) must still resolve
        # — :func:`template_for_slug` runs them through the alias
        # shim so existing prefs don't end up with a blank overlay.
        tpl = template_for_slug("dailies_default")
        assert tpl == builtin_template("default")

    def test_legacy_user_template_still_wins(self, _user_dir) -> None:
        # A user template saved under the legacy slug (because the
        # user upgraded from a pre-1.7 build) must still be found
        # under that slug — the rename can't strip out files the
        # user explicitly authored.
        custom = BurninTemplate(name="user-legacy")
        save_user_template("dailies_default", custom)
        assert template_for_slug("dailies_default").name == "user-legacy"


# ---------------------------------------------------------------------- Save / delete

class TestSaveDelete:
    def test_save_creates_dir_on_first_use(self, _user_dir, tmp_path) -> None:
        # ``_user_dir`` is per-test fresh; first save must mkdir.
        assert not _user_dir.exists()
        save_user_template("first", builtin_template("default"))
        assert _user_dir.exists()
        assert (_user_dir / "first.burnin.json").is_file()

    def test_save_round_trip(self, _user_dir) -> None:
        original = builtin_template("default")
        save_user_template("my_round_trip", original)
        restored = template_for_slug("my_round_trip")
        assert restored == original

    def test_is_user_template_true_after_save(self, _user_dir) -> None:
        assert is_user_template("default") is False
        save_user_template("custom", builtin_template("default"))
        assert is_user_template("custom") is True

    def test_delete_removes_file(self, _user_dir) -> None:
        save_user_template("temp", builtin_template("default"))
        assert is_user_template("temp") is True
        assert delete_user_template("temp") is True
        assert is_user_template("temp") is False

    def test_delete_falls_back_to_builtin(self, _user_dir) -> None:
        # User overrides the default builtin then deletes the
        # override — the slug stays valid via the builtin.
        save_user_template("default", BurninTemplate(name="override"))
        assert template_for_slug("default").name == "override"
        delete_user_template("default")
        assert template_for_slug("default").name == "Default"  # the builtin's name

    def test_delete_nonexistent_returns_false(self, _user_dir) -> None:
        assert delete_user_template("ghost") is False


# ---------------------------------------------------------------------- Shared library

class TestSharedDirProvider:
    """The storage layer reads the shared dir through an installable
    provider callable so it doesn't have to import :mod:`preferences`.
    Pin the contract: no provider → no shared dir, provider returning
    empty string → no shared dir, provider returning a real path →
    shared dir is that path."""

    def test_no_provider_no_shared_dir(self) -> None:
        # Fresh process, no provider installed → ``None``.
        set_shared_dir_provider(None)
        try:
            assert shared_burnins_dir() is None
        finally:
            set_shared_dir_provider(None)

    def test_provider_returning_empty_is_no_shared_dir(self) -> None:
        set_shared_dir_provider(lambda: "")
        try:
            assert shared_burnins_dir() is None
        finally:
            set_shared_dir_provider(None)

    def test_provider_pointing_at_missing_dir_is_no_shared_dir(
        self, tmp_path,
    ) -> None:
        set_shared_dir_provider(lambda: str(tmp_path / "ghost"))
        try:
            assert shared_burnins_dir() is None
        finally:
            set_shared_dir_provider(None)

    def test_provider_pointing_at_real_dir_resolves(
        self, _shared_dir,
    ) -> None:
        # Configured + exists → returns the path.
        out = shared_burnins_dir()
        assert out is not None
        assert out == _shared_dir


class TestSharedListing:
    def test_list_shared_empty_without_provider(self) -> None:
        set_shared_dir_provider(None)
        try:
            assert list_shared_templates() == []
        finally:
            set_shared_dir_provider(None)

    def test_list_shared_picks_up_dropped_files(
        self, _shared_dir,
    ) -> None:
        # Save through the public helper (which round-trips JSON).
        save_shared_template("team_default", builtin_template("default"))
        save_shared_template("alt_layout", builtin_template("default"))
        slugs = [slug for slug, _ in list_shared_templates()]
        assert slugs == ["alt_layout", "team_default"]

    def test_list_all_slugs_orders_user_then_shared_then_builtin(
        self, _user_dir, _shared_dir,
    ) -> None:
        # Set up: one local, one shared, plus the shipped builtin.
        save_user_template("my_local", builtin_template("default"))
        save_shared_template("team_one", builtin_template("default"))
        combined = list_all_slugs()
        # User templates come first (sorted within their tier),
        # then shared, then builtins.
        assert combined[0] == "my_local"
        assert combined[1] == "team_one"
        assert combined[-1] == "default"  # the builtin

    def test_list_all_slugs_user_shadows_shared(
        self, _user_dir, _shared_dir,
    ) -> None:
        # Saving locally under a slug that also exists in shared
        # must NOT show two entries — the local copy wins, shared
        # is hidden.
        save_shared_template("brand", builtin_template("default"))
        save_user_template("brand", builtin_template("default"))
        combined = list_all_slugs()
        assert combined.count("brand") == 1


class TestSharedResolution:
    def test_template_for_slug_picks_shared_when_no_user(
        self, _user_dir, _shared_dir,
    ) -> None:
        # User dir empty → shared wins for non-builtin slugs.
        custom = BurninTemplate(name="team-marker")
        save_shared_template("teamfork", custom)
        out = template_for_slug("teamfork")
        assert out.name == "team-marker"

    def test_user_wins_over_shared(
        self, _user_dir, _shared_dir,
    ) -> None:
        # User shadows a shared template under the same slug.
        save_shared_template(
            "brand", BurninTemplate(name="team-version"),
        )
        save_user_template(
            "brand", BurninTemplate(name="my-override"),
        )
        out = template_for_slug("brand")
        assert out.name == "my-override"

    def test_shared_wins_over_builtin(
        self, _user_dir, _shared_dir,
    ) -> None:
        # A shared template under the same slug as a builtin
        # shadows the builtin (so a team can ship its own "default").
        save_shared_template(
            "default", BurninTemplate(name="team-default"),
        )
        out = template_for_slug("default")
        assert out.name == "team-default"

    def test_is_user_template_false_for_shared(
        self, _user_dir, _shared_dir,
    ) -> None:
        # A slug that lives only in shared MUST NOT report as
        # ``is_user_template`` — the editor uses this to decide
        # which on-disk file Save / Delete write to.
        save_shared_template("teamonly", builtin_template("default"))
        assert is_user_template("teamonly") is False

    def test_is_shared_template_true_only_when_only_shared(
        self, _user_dir, _shared_dir,
    ) -> None:
        save_shared_template("teamonly", builtin_template("default"))
        assert is_shared_template("teamonly") is True
        # User shadows it → it's no longer "shared only".
        save_user_template("teamonly", builtin_template("default"))
        assert is_shared_template("teamonly") is False


class TestSharedSaveDelete:
    def test_save_shared_creates_dir(self, monkeypatch, tmp_path) -> None:
        # The shared root may not exist yet (admin set the pref but
        # hasn't created the folder yet). ``save_shared_template``
        # is expected to mkdir it on first save.
        shared = tmp_path / "newshare" / "nested"
        set_shared_dir_provider(lambda: str(shared))
        try:
            save_shared_template("first", builtin_template("default"))
            assert (shared / "first.burnin.json").is_file()
        finally:
            set_shared_dir_provider(None)

    def test_save_shared_without_provider_raises(self) -> None:
        set_shared_dir_provider(None)
        try:
            with pytest.raises(RuntimeError, match="No shared"):
                save_shared_template("x", builtin_template("default"))
        finally:
            set_shared_dir_provider(None)

    def test_delete_shared_removes_file(self, _shared_dir) -> None:
        save_shared_template("temp", builtin_template("default"))
        assert is_shared_template("temp") is True
        assert delete_shared_template("temp") is True
        assert is_shared_template("temp") is False

    def test_delete_user_does_not_touch_shared(
        self, _user_dir, _shared_dir,
    ) -> None:
        # Both saved under the same slug. Deleting the LOCAL copy
        # must leave the shared one alone — otherwise reviewers
        # could accidentally wipe team files by dropping their
        # local overrides.
        save_shared_template("brand", builtin_template("default"))
        save_user_template("brand", builtin_template("default"))
        assert delete_user_template("brand") is True
        assert is_shared_template("brand") is True
        # The slug now resolves via shared.
        assert template_for_slug("brand") == builtin_template("default")
