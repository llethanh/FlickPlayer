"""Burnin template data model — frozen dataclasses + JSON I/O.

A :class:`BurninTemplate` carries two :class:`BurninBar` instances
(top and bottom). Each bar holds an ordered tuple of elements drawn
inside it; element types are :class:`TextElement`,
:class:`ImageElement` and :class:`SpacerElement`. Everything is
``frozen=True`` so a template snapshot can be safely shared across
threads (the renderer runs on the GL upload path, the editor lives
on the Qt thread).

Coordinates
-----------
Within a bar, each element has:

- ``anchor`` — ``"left"`` / ``"center"`` / ``"right"`` — which side
  of the bar it anchors to. The renderer lays elements with the
  same anchor in declaration order.
- ``offset_x`` / ``offset_y`` — pixel offset from the anchor
  position. ``offset_x`` is positive rightward; ``offset_y`` is
  positive downward (= deeper into the bar). All offsets are
  measured at the **bar's native height** — when the bar height
  changes (different image resolutions, contact-sheet tiles), the
  renderer scales them proportionally.

Bar geometry
------------
``BurninBar.height_pct`` is the fraction of the image height the
bar occupies (e.g. ``0.06`` = 6 %). At a 1080-line image that's
~65 px; at a 270-line CS tile that's ~16 px. The renderer enforces
a hard minimum (``MIN_BAR_PX = 16``) so tile burnins stay legible.

JSON layout
-----------
Templates are stored as ``*.burnin.json`` — JSON because it nests
naturally (template → bar → elements) and matches the rest of
Flick's user-facing files (sessions, annotation sidecars). The
``type`` discriminator on each element drives the loader's
dispatch.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal, Union

# --- Constants --------------------------------------------------------------

# Anchors are limited to horizontal because vertical anchoring inside a
# thin bar would just confuse layout — bars are tall enough for one row.
Anchor = Literal["left", "center", "right"]

# Element discriminator values.
ELEMENT_TYPES = ("text", "image", "spacer")

# Hard floor on bar pixel height — drives the legibility check the
# renderer applies on CS tiles. Below this the bar can't even fit
# 8 pt text + 2 px padding, so it's better to clamp than to render
# unreadably small.
MIN_BAR_PX = 16


# --- Element dataclasses ----------------------------------------------------

@dataclass(frozen=True)
class _ElementBase:
    """Fields shared by every burnin element kind.

    Kept as a base class rather than inlined into each child so the
    list-layout logic in the renderer can sort by ``anchor`` without
    type-checking the element first.
    """

    anchor: Anchor = "left"
    offset_x: float = 0.0
    offset_y: float = 0.0


@dataclass(frozen=True)
class TextElement(_ElementBase):
    """A text token resolved through :mod:`img_player.burnins.tokens`.

    ``text`` may contain placeholders like ``"{frame}/{frame_total}"``
    — the renderer asks ``tokens.resolve`` to expand them against the
    per-render context before drawing.

    ``font_size_pt`` is the **design** size at the template's
    reference bar height; the renderer scales it proportionally when
    the actual bar pixel height differs.
    """

    text: str = "{sequence}"
    font_family: str = "Inter"
    font_size_pt: int = 12
    font_weight: Literal["normal", "bold"] = "normal"
    color: str = "#FFE5C0"


@dataclass(frozen=True)
class ImageElement(_ElementBase):
    """A bitmap (PNG / JPG / …) — typically a studio logo.

    ``path`` may be absolute or interpreted relative to the template
    file. ``height_pct`` is the image height as a fraction of the
    bar height; width scales to preserve aspect.
    """

    path: str = ""
    height_pct: float = 0.8


@dataclass(frozen=True)
class SpacerElement(_ElementBase):
    """A horizontal gap. Useful between two elements that share the
    same anchor when the template wants a visible separation."""

    width_px: int = 16


BurninElement = Union[TextElement, ImageElement, SpacerElement]


# --- Bar + template ---------------------------------------------------------

@dataclass(frozen=True)
class BurninBar:
    """A single info bar (top OR bottom). Disabled bars render as
    nothing — set ``enabled=False`` to hide the bar without losing
    the elements you've configured."""

    enabled: bool = True
    # Fraction of the image height the bar occupies. 0.06 = 6 % which
    # is the sweet spot from the screenshot review (~65 px on 1080).
    height_pct: float = 0.06
    # Bar background — semi-transparent dark by default so the text
    # reads clearly against bright frames without hiding the image.
    bg_color: str = "rgba(20, 20, 22, 0.85)"
    elements: tuple[BurninElement, ...] = ()


@dataclass(frozen=True)
class BurninTemplate:
    """The full template — name + the two bars."""

    name: str = "Untitled"
    description: str = ""
    top_bar: BurninBar = field(default_factory=BurninBar)
    bottom_bar: BurninBar = field(default_factory=BurninBar)


# --- JSON I/O ---------------------------------------------------------------

_ELEMENT_CLASSES: dict[str, type[BurninElement]] = {
    "text": TextElement,
    "image": ImageElement,
    "spacer": SpacerElement,
}

_TYPE_FOR_CLASS: dict[type[BurninElement], str] = {
    TextElement: "text",
    ImageElement: "image",
    SpacerElement: "spacer",
}


def _element_to_dict(elem: BurninElement) -> dict[str, Any]:
    """Serialise an element with a ``type`` discriminator first."""
    out: dict[str, Any] = {"type": _TYPE_FOR_CLASS[type(elem)]}
    out.update(asdict(elem))
    return out


def _element_from_dict(data: dict[str, Any]) -> BurninElement:
    """Loader dispatch. Unknown keys are dropped silently — that's
    forward-compat: a future field added by a newer Flick won't break
    loading on an older one. Unknown ``type`` raises (the template is
    structurally broken)."""
    type_tag = data.get("type")
    if type_tag not in _ELEMENT_CLASSES:
        raise ValueError(
            f"Unknown burnin element type: {type_tag!r}. "
            f"Expected one of {ELEMENT_TYPES}."
        )
    cls = _ELEMENT_CLASSES[type_tag]
    # Keep only the fields the dataclass actually declares — drops
    # the ``type`` discriminator and any forward-compat unknowns.
    valid_names = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in valid_names}
    return cls(**kwargs)


def _bar_to_dict(bar: BurninBar) -> dict[str, Any]:
    return {
        "enabled": bar.enabled,
        "height_pct": bar.height_pct,
        "bg_color": bar.bg_color,
        "elements": [_element_to_dict(e) for e in bar.elements],
    }


def _bar_from_dict(data: dict[str, Any]) -> BurninBar:
    elements = tuple(
        _element_from_dict(e) for e in data.get("elements", [])
    )
    # Same "drop unknowns" pattern as elements: tolerate forward-compat
    # fields, only construct with what BurninBar knows about.
    return BurninBar(
        enabled=bool(data.get("enabled", True)),
        height_pct=float(data.get("height_pct", BurninBar.height_pct)),
        bg_color=str(data.get("bg_color", BurninBar.bg_color)),
        elements=elements,
    )


def template_to_dict(tpl: BurninTemplate) -> dict[str, Any]:
    """Serialise a template to a plain dict — useful for tests and for
    embedding a template in another JSON document (e.g. a session)."""
    return {
        "name": tpl.name,
        "description": tpl.description,
        "top_bar": _bar_to_dict(tpl.top_bar),
        "bottom_bar": _bar_to_dict(tpl.bottom_bar),
    }


def template_from_dict(data: dict[str, Any]) -> BurninTemplate:
    """Reverse of :func:`template_to_dict`. Missing bars default to a
    disabled bar with no elements (i.e. "no burnin on this side")."""
    return BurninTemplate(
        name=str(data.get("name", "Untitled")),
        description=str(data.get("description", "")),
        top_bar=_bar_from_dict(data.get("top_bar", {})),
        bottom_bar=_bar_from_dict(data.get("bottom_bar", {})),
    )


def save_template(template: BurninTemplate, path: Path | str) -> None:
    """Atomic JSON write — temp file in the same directory + replace.

    Same pattern as the user-prefs / annotation sidecar saves: write
    to a sibling ``.tmp`` then rename atomically, so a crash mid-save
    can't corrupt an existing template. Pretty-printed for hand-
    editability.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = template_to_dict(template)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp_path.replace(path)


def load_template(path: Path | str) -> BurninTemplate:
    """Load a template from disk. ``FileNotFoundError`` propagates
    (the caller decides how to handle a missing template — typically
    fall back to a builtin). JSON parse errors propagate too."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(
            f"Burnin template at {path} is not a JSON object."
        )
    return template_from_dict(data)
