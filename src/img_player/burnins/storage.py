"""Disk layout for burnin templates: builtin + user.

A *user template* is a ``*.burnin.json`` file under
:func:`img_player.app_paths.user_burnins_dir` (= the user's roaming
appdata). The slug is the filename without ``.burnin.json`` — same
slug the View menu's "Active burnin template" submenu emits and the
App's preferences persist.

Resolution order
----------------

Slugs map to templates via a 2-tier lookup:

1. **User template** — if a file matches ``<slug>.burnin.json`` in
   the user dir, load it. Lets the user OVERRIDE a builtin by
   saving a template under the same slug (same convention as the
   prefs system).
2. **Builtin** — fall back to the shipped builtin if it exists.

Unknown slug raises ``KeyError`` so callers can decide what to do
(usually fall back to ``dailies_default``).

Filenames are slugged via :func:`slugify` so a human-typed name
("Studio dailies v2!") yields a filesystem-safe slug
(``"studio_dailies_v2"``) without surprising the user.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from img_player.app_paths import user_burnins_dir
from img_player.burnins.builtins import BUILTINS, builtin_template
from img_player.burnins.model import (
    BurninTemplate,
    load_template,
    save_template,
)

_TEMPLATE_SUFFIX = ".burnin.json"

# Slugs that map to builtins — preserved for use-cases where we need
# to differentiate "user shadowed this builtin" vs. "pure builtin".
BUILTIN_SLUGS: tuple[str, ...] = tuple(BUILTINS.keys())


# ---------------------------------------------------------------------- Slug helpers

_SLUG_INVALID = re.compile(r"[^a-z0-9_-]+")


def slugify(name: str) -> str:
    """Normalise a user-typed name into a filesystem-safe slug.

    Lowercase, ASCII-ish (we keep ``[a-z0-9_-]``), runs of invalid
    characters collapse to a single ``_``. Empty input or all-invalid
    input returns ``"untitled"`` — saves the caller from explicitly
    handling that edge case.
    """
    # ``[^a-z0-9_-]+`` matches RUNS of invalid characters, so
    # consecutive spaces / punctuation collapse to a single ``_``
    # rather than ``foo___bar``.
    s = name.strip().lower()
    s = _SLUG_INVALID.sub("_", s)
    s = s.strip("_-")
    return s or "untitled"


# ---------------------------------------------------------------------- Listing / lookup

def _user_template_path(slug: str) -> Path:
    return user_burnins_dir() / f"{slug}{_TEMPLATE_SUFFIX}"


# ----- Shared (team) library --------------------------------------

# The shared library is an OPTIONAL second source: a folder (often on
# a network share) that the team configures via the editor's
# "Shared folder…" button. Every member points at the same path and
# the templates they all see in their menu come from the same place.
# Stored as a process-level variable so storage tests can override it
# without touching :mod:`preferences` — the App wires
# :func:`set_shared_dir_provider` at boot to read the live pref.

_shared_dir_provider: Callable[[], str] | None = None


def set_shared_dir_provider(
    provider: Callable[[], str] | None,
) -> None:
    """Install a callable that returns the current shared-burnins
    directory as a string (or ``""`` when none is set). The App
    points this at ``lambda: self._prefs.burnin_shared_dir`` at
    boot so storage reads the live preference without having to
    import the prefs layer (keeps :mod:`storage` free of UI
    dependencies). Pass ``None`` to clear (handy in tests)."""
    global _shared_dir_provider
    _shared_dir_provider = provider


def shared_burnins_dir() -> Path | None:
    """Resolve the configured shared-templates directory, or
    ``None`` when none is set / the path doesn't exist / no provider
    has been installed. Probing for ``is_dir`` here means a stale
    network mount silently drops to "no shared templates" rather
    than throwing in the editor's combo refresh."""
    if _shared_dir_provider is None:
        return None
    raw = _shared_dir_provider() or ""
    if not raw:
        return None
    try:
        path = Path(raw)
    except (TypeError, ValueError):
        return None
    return path if path.is_dir() else None


def _shared_template_path(slug: str) -> Path | None:
    base = shared_burnins_dir()
    if base is None:
        return None
    return base / f"{slug}{_TEMPLATE_SUFFIX}"


def list_user_templates() -> list[tuple[str, Path]]:
    """Return ``[(slug, path)]`` for every ``*.burnin.json`` the user
    has saved locally, sorted by slug. Missing directory → empty
    list (the first save creates the dir)."""
    return _list_templates_in(user_burnins_dir())


def list_shared_templates() -> list[tuple[str, Path]]:
    """Return ``[(slug, path)]`` for every ``*.burnin.json`` in the
    configured shared directory, sorted by slug. Empty list when no
    shared dir is set or the directory doesn't exist."""
    base = shared_burnins_dir()
    return _list_templates_in(base) if base is not None else []


def _list_templates_in(base: Path) -> list[tuple[str, Path]]:
    if not base.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for p in base.iterdir():
        if not p.is_file():
            continue
        if not p.name.endswith(_TEMPLATE_SUFFIX):
            continue
        slug = p.name[: -len(_TEMPLATE_SUFFIX)]
        out.append((slug, p))
    out.sort(key=lambda x: x[0])
    return out


def list_all_slugs() -> list[str]:
    """Every slug the editor's combo should show — user templates
    first (sorted), then shared templates the user hasn't already
    shadowed locally, then builtins. Order encodes precedence: any
    local copy wins over the team library, which in turn wins over
    the shipped builtin."""
    user_slugs = [slug for slug, _ in list_user_templates()]
    user_set = set(user_slugs)
    shared_slugs = [
        slug for slug, _ in list_shared_templates()
        if slug not in user_set
    ]
    seen = user_set | set(shared_slugs)
    builtin_slugs = [s for s in BUILTINS if s not in seen]
    return user_slugs + shared_slugs + builtin_slugs


def template_for_slug(slug: str) -> BurninTemplate:
    """Resolve ``slug`` → :class:`BurninTemplate`.

    Precedence: user (local) → shared (team) → builtin. Lets a
    reviewer override the team's template by saving locally under
    the same slug without changing what the rest of the team sees.
    Unknown slugs raise ``KeyError`` — callers typically fall back
    to ``default`` via
    :func:`img_player.burnins.builtins.builtin_template`.

    Legacy slugs (``dailies_default``, ``minimal``, ``studio_banner``
    shipped pre-1.7) get migrated through
    :func:`img_player.burnins.builtins.resolve_slug` before the
    lookup, so prefs / sessions persisted under the old names still
    resolve cleanly.
    """
    from img_player.burnins.builtins import resolve_slug  # noqa: PLC0415
    # Try the slug as-is in user first, then shared, so files saved
    # under the legacy name still win (the rename shouldn't strip
    # out files the user explicitly authored).
    user_path = _user_template_path(slug)
    if user_path.is_file():
        return load_template(user_path)
    shared_path = _shared_template_path(slug)
    if shared_path is not None and shared_path.is_file():
        return load_template(shared_path)
    canonical = resolve_slug(slug)
    if canonical != slug:
        # Also probe the canonical user / shared paths — covers the
        # case where the user saved under the new name but a session
        # referenced the legacy one.
        canonical_user = _user_template_path(canonical)
        if canonical_user.is_file():
            return load_template(canonical_user)
        canonical_shared = _shared_template_path(canonical)
        if canonical_shared is not None and canonical_shared.is_file():
            return load_template(canonical_shared)
    if canonical in BUILTINS:
        return builtin_template(canonical)
    raise KeyError(
        f"No burnin template found for slug {slug!r}. "
        f"Known builtins: {tuple(BUILTINS.keys())!r}.",
    )


def is_user_template(slug: str) -> bool:
    """``True`` if the slug corresponds to a saved user template (and
    thus is deletable / overwritable from the editor). Shared
    templates report ``False`` here — see :func:`is_shared_template`
    — so the editor can show different actions for "your local"
    vs "team library" entries."""
    return _user_template_path(slug).is_file()


def is_shared_template(slug: str) -> bool:
    """``True`` if the slug only resolves via the shared directory
    (i.e. there's no local user copy shadowing it). The editor uses
    this to tag the menu / combo entry and to default "Save" to the
    shared dir when editing a shared template."""
    if _user_template_path(slug).is_file():
        return False
    shared = _shared_template_path(slug)
    return shared is not None and shared.is_file()


def save_user_template(slug: str, template: BurninTemplate) -> Path:
    """Save ``template`` as a user template under ``slug`` and return
    the path it landed at. Creates the user dir on first save."""
    path = _user_template_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_template(template, path)
    return path


def save_shared_template(slug: str, template: BurninTemplate) -> Path:
    """Save ``template`` to the configured shared directory under
    ``slug``. Raises :class:`RuntimeError` when no shared dir is
    configured / the dir isn't writable — the editor surfaces this
    as a message box rather than silently swallowing the click.
    Creates the shared dir on first save if the path exists in its
    parent (so an admin can pre-create the share path and the first
    "Save to shared" populates it)."""
    base = shared_burnins_dir()
    if base is None:
        # Try one more time without the is_dir gate — the user may
        # have just configured a path that doesn't exist yet.
        raw = _shared_dir_provider() if _shared_dir_provider else ""
        if not raw:
            raise RuntimeError(
                "No shared burnin directory configured.",
            )
        base = Path(raw)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create shared burnin directory {base!s}: {exc}",
        ) from exc
    path = base / f"{slug}{_TEMPLATE_SUFFIX}"
    save_template(template, path)
    return path


def delete_user_template(slug: str) -> bool:
    """Remove a user template file. Returns ``True`` if it existed
    and was removed, ``False`` otherwise (so the caller can warn).
    Builtins + shared templates can't be deleted here — for shared
    use :func:`delete_shared_template` (so we never wipe a team
    file by accident in the local-delete path)."""
    path = _user_template_path(slug)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def delete_shared_template(slug: str) -> bool:
    """Remove a shared template file. Returns ``True`` on success,
    ``False`` if the slug doesn't resolve there or the unlink fails
    (permission denied on a read-only share, network drop)."""
    path = _shared_template_path(slug)
    if path is None or not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True
