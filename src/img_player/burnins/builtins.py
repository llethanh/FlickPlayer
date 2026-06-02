"""Builtin burnin templates — shipped with the app so the user has
something useful out of the box without touching the editor.

A single preset is shipped:

* **default** — the workhorse VFX-dailies burnin: sequence name +
  frame counter top, user + date bottom. Both bars on the default
  semi-transparent dark background, accent-orange title. The user
  is expected to ship custom templates via the editor; anything
  shipped here is a "first launch — give me something to read"
  fallback.

The dict uses the same shape :func:`model.template_from_dict`
consumes, so loading the builtin is just
``template_from_dict(BUILTINS["default"])``.

Legacy slug ``"dailies_default"`` resolves to the same template
via :data:`LEGACY_SLUG_ALIASES` so existing user prefs / saved
sessions keep working after the rename.
"""

from __future__ import annotations

from typing import Any

# Charter colours mirroring ui/theme.py so a builtin burnin looks
# native against the Flick player without any further config.
_ACC_BRIGHT = "#F5A830"   # H.ACC_BRIGHT — accent orange
_T_CREAM = "#FFE5C0"      # same warm cream as the legacy info band
# Bar background: accent orange at ~75 % opacity. Reads as a warm
# tinted band over the image without obliterating it — matches the
# look the user dialled in via the editor.
_BG_BAR = "rgba(245, 168, 48, 0.75)"


# ---------------------------------------------------------------------------
# Builtin templates
# ---------------------------------------------------------------------------

# Element typography — uniform across the template so the burnin
# reads as a single ribbon, not four mismatched fonts. Matches the
# settings the user dialled in via the editor: JetBrains Mono Bold
# at 16 pt in warm cream over the orange bar.
_FONT = "JetBrains Mono"
_FONT_SIZE = 16
_FONT_WEIGHT = "bold"


def _text(anchor: str, offset_x: int, text: str) -> dict[str, Any]:
    """Build a uniformly-styled text element. Keeps the dict
    declarations below short and forces a single source of truth
    for the default font + colour."""
    return {
        "type": "text",
        "anchor": anchor,
        "offset_x": offset_x,
        "offset_y": 0,
        "text": text,
        "font_family": _FONT,
        "font_size_pt": _FONT_SIZE,
        "font_weight": _FONT_WEIGHT,
        "color": _T_CREAM,
    }


_DEFAULT: dict[str, Any] = {
    "name": "Default",
    "description": (
        "Sequence + master & layer frame counters at the top, user "
        "+ date and resolution / fps at the bottom. Cream JetBrains "
        "Mono Bold over an orange ribbon — the player's house style."
    ),
    "top_bar": {
        "enabled": True,
        "height_pct": 0.04,
        "bg_color": _BG_BAR,
        "elements": [
            _text("left", 16, "{sequence}"),
            # Master frame on the left + layer (source) frame on the
            # right of the same readout — gives the reviewer both
            # the timeline frame they use to take notes and the
            # source frame they need to find the plate on disk.
            _text(
                "right", -16,
                "frame {frame}/{frame_total}"
                "  ·  layer {layer_frame}/{layer_frame_total}",
            ),
        ],
    },
    "bottom_bar": {
        "enabled": True,
        "height_pct": 0.04,
        "bg_color": _BG_BAR,
        "elements": [
            _text("left", 16, "{user}  ·  {date}"),
            _text("right", -16, "{resolution}  ·  {fps} fps"),
        ],
    },
}


BUILTINS: dict[str, dict[str, Any]] = {
    "default": _DEFAULT,
}
"""``{slug: template_dict}`` for every shipped template. The slug is
also used as the on-disk filename in
``%APPDATA%/FlickPlayer/burnins/`` — a user-customised version of a
builtin lives under the same slug so it cleanly shadows the shipped
one."""


LEGACY_SLUG_ALIASES: dict[str, str] = {
    "dailies_default": "default",
    # ``minimal`` and ``studio_banner`` were shipped in v1.5.x and
    # 1.6.x as the other two builtins; we dropped them in 1.7.x to
    # keep the menu focused on a single "ship the orange-on-dark
    # template" preset. If someone's prefs still reference them we
    # fall back to the new default rather than failing.
    "minimal": "default",
    "studio_banner": "default",
}
"""Maps slugs that used to ship as builtins (or were the default
slug under a previous name) to their current resolution. Looked up
by :func:`resolve_slug` so callers don't need to know the rename
history."""


def resolve_slug(slug: str) -> str:
    """Map a possibly-legacy slug to its current canonical form.

    Returns ``slug`` unchanged when it's already current. Doesn't
    validate that the result is a known builtin — that's the
    caller's job (storage lookup may resolve to a user template
    saved under the legacy slug). The point here is just to
    transparently migrate prefs / sessions saved under the old
    names.
    """
    return LEGACY_SLUG_ALIASES.get(slug, slug)


def builtin_template(slug: str):  # type: ignore[no-untyped-def]
    """Return a fresh :class:`BurninTemplate` for the builtin slug.

    Accepts both the current canonical slug (``"default"``) and any
    legacy alias listed in :data:`LEGACY_SLUG_ALIASES`. Raises
    ``KeyError`` if the slug isn't recognised at all. Local import
    of ``template_from_dict`` keeps the builtins module free of
    cycles when ``model`` imports anything that touches
    ``builtins``.
    """
    from img_player.burnins.model import template_from_dict
    canonical = resolve_slug(slug)
    if canonical not in BUILTINS:
        raise KeyError(
            f"Unknown builtin burnin template: {slug!r}. "
            f"Known: {tuple(BUILTINS.keys())!r}.",
        )
    return template_from_dict(BUILTINS[canonical])
