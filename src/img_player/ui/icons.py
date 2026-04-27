"""Inline SVG icon factory — single source for transport / panel icons.

Icons are stored as small XML strings with a ``{color}`` placeholder.
At call time they're rendered into a ``QPixmap`` via ``QSvgRenderer``
and wrapped as ``QIcon``. Two reasons we keep them inline rather than
shipping ``.svg`` files:

* No PyInstaller ``datas`` plumbing — ``importlib.resources`` doesn't
  work nicely from a frozen bundle without explicit hooks. Strings in
  Python source travel for free.
* Coloured icons. The play button needs ``ACCENT`` orange, the rest
  use ``TEXT_PRIMARY``. Doing this with on-disk SVG files would
  require either editing them on the fly or shipping one file per
  colour.

The icons themselves match the mockup's geometry (16×16 viewBox).

Usage::

    from img_player.ui.icons import make_icon
    from img_player.ui.theme import H

    play_icon  = make_icon("play",  color=H.ACCENT)
    stop_icon  = make_icon("stop")  # default = TEXT_PRIMARY
    pause_icon = make_icon("pause")
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from img_player.ui.theme import H


# ----------------------------------------------------------------------- Templates

# Each template uses a 16×16 viewBox so they all line up on the same grid.
# The ``{color}`` placeholder gets ``str.format``-ed at call time.
# Geometry comes straight from `ui_mockup.html` — keep it aligned.
_TEMPLATES: dict[str, str] = {
    # Triangle pointing right.
    "play": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="4,2 14,8 4,14" fill="{color}"/>'
        "</svg>"
    ),
    # Mirror of "play" — same triangle pointing left for reverse play.
    "play_reverse": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="12,2 2,8 12,14" fill="{color}"/>'
        "</svg>"
    ),
    # Two vertical bars (the natural counterpart to play). Geometry
    # chosen so the two bars together occupy roughly the same visual
    # mass as the play triangle.
    "pause": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="2" width="3" height="12" fill="{color}"/>'
        '<rect x="9" y="2" width="3" height="12" fill="{color}"/>'
        "</svg>"
    ),
    # Rounded square.
    "stop": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="3" y="3" width="10" height="10" rx="1" fill="{color}"/>'
        "</svg>"
    ),
    # Step backward: main triangle pointing left + smaller dim triangle
    # to suggest "skip".
    "prev": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="12,2 4,8 12,14" fill="{color}"/>'
        '<polygon points="6,2 4,8 6,14" fill="{color}" opacity="0.5"/>'
        "</svg>"
    ),
    # Step forward, mirror of "prev".
    "next": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="4,2 12,8 4,14" fill="{color}"/>'
        '<polygon points="10,2 12,8 10,14" fill="{color}" opacity="0.5"/>'
        "</svg>"
    ),
    # Jump to first frame: vertical bar on the left + leftward triangle.
    "first": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="2" y="2" width="2" height="12" rx="1" fill="{color}"/>'
        '<polygon points="14,2 6,8 14,14" fill="{color}"/>'
        "</svg>"
    ),
    # Jump to last frame, mirror of "first".
    "last": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="12" y="2" width="2" height="12" rx="1" fill="{color}"/>'
        '<polygon points="2,2 10,8 2,14" fill="{color}"/>'
        "</svg>"
    ),
    # Hamburger / dock toggle. Three horizontal bars at y=3, y=7, y=11
    # (height 2 each → centres at 4 / 8 / 12), so the gaps above/below
    # each bar are identical. The Unicode glyph U+2630 we used to
    # render here had inconsistent spacing across system fonts; an
    # SVG primitive is the only way to guarantee even bars.
    "menu": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="3" y="3" width="10" height="2" rx="1" fill="{color}"/>'
        '<rect x="3" y="7" width="10" height="2" rx="1" fill="{color}"/>'
        '<rect x="3" y="11" width="10" height="2" rx="1" fill="{color}"/>'
        "</svg>"
    ),
    # ===== Annotation icons (slice 4 polish) =============================
    # Pen — diagonal body pointing top-right with a separate nib cap so
    # the silhouette reads as "drawing tool" not "knife". Body uses the
    # main fill, the nib uses the same fill so it merges optically into
    # one shape.
    "pen": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="2,14 9,7 11,9 4,16" fill="{color}"/>'
        '<polygon points="9,7 12,4 14,6 11,9" fill="{color}"/>'
        '<polygon points="2,14 4,16 1,15" fill="{color}"/>'
        "</svg>"
    ),
    # Eraser — angled block, two-tone via opacity to suggest the
    # rubber/holder split classic on review-tool eraser icons.
    "eraser": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="3,11 9,5 13,9 7,15" fill="{color}"/>'
        '<polygon points="9,5 11,3 14,6 13,9" fill="{color}" opacity="0.55"/>'
        "</svg>"
    ),
    # Undo — 3/4-circle arc + arrowhead at the start. We trace the arc
    # with a path stroke for visual continuity (other icons are filled,
    # but a stroke arc reads "circular motion" much better than any
    # polygon approximation).
    "undo": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M 4 4 A 5 5 0 1 1 3 8.5" '
        'stroke="{color}" stroke-width="1.6" fill="none" stroke-linecap="round"/>'
        '<polygon points="2,5 7,3 5,8" fill="{color}"/>'
        "</svg>"
    ),
    # Redo — mirror of undo across the vertical axis.
    "redo": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M 12 4 A 5 5 0 1 0 13 8.5" '
        'stroke="{color}" stroke-width="1.6" fill="none" stroke-linecap="round"/>'
        '<polygon points="14,5 9,3 11,8" fill="{color}"/>'
        "</svg>"
    ),
    # Pin / thumbtack viewed from the side — head at the top with
    # "wings" flaring out, narrowing to a point at the bottom. Used by
    # the toolbar's float ⇄ dock toggle.
    "pin": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M 8 2 L 11 5 L 11 9 L 13 11 L 9 11 L 8 14 L 7 11 L 3 11 L 5 9 L 5 5 Z" '
        'fill="{color}"/>'
        "</svg>"
    ),
    # Skip-to-previous-annotated-frame. A leftward triangle with a
    # round dot at the destination side (left), so it reads as
    # "jump to a marker on the left" rather than "step back one
    # frame" (which is the existing ``prev`` icon).
    "annotation_prev": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="13,3 6,8 13,13" fill="{color}"/>'
        '<circle cx="3" cy="8" r="1.6" fill="{color}"/>'
        "</svg>"
    ),
    # Skip-to-next-annotated-frame, mirror of ``annotation_prev``.
    "annotation_next": (
        '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">'
        '<polygon points="3,3 10,8 3,13" fill="{color}"/>'
        '<circle cx="13" cy="8" r="1.6" fill="{color}"/>'
        "</svg>"
    ),
}


# ----------------------------------------------------------------------- Factory


def _device_pixel_ratio() -> float:
    """Return the active screen's DPR if a QApplication exists, else 1.0.

    Used to render icons at 2× on hi-DPI displays so they stay sharp
    when Qt scales them down.
    """
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    if screen is None:
        return 1.0
    return float(screen.devicePixelRatio())


@lru_cache(maxsize=64)
def make_icon(name: str, color: str = H.TEXT_PRIMARY, size: int = 18) -> QIcon:
    """Return a ``QIcon`` for a named template, painted in ``color``.

    Parameters
    ----------
    name:
        One of the keys in ``_TEMPLATES`` (e.g. ``"play"``).
    color:
        Hex string used as the SVG ``fill``. Default is the charter's
        primary text colour. Pass ``H.ACCENT`` for the play button.
    size:
        Logical pixel size of the resulting icon. Hi-DPI handling is
        automatic — on a 200 % display we render at ``size * dpr`` and
        attach the DPR to the pixmap so Qt downscales cleanly.

    Caching: the icon is memoized on ``(name, color, size)``. We expect
    at most a few dozen unique combinations across the whole app.
    """
    template = _TEMPLATES[name]
    xml = template.format(color=color)
    renderer = QSvgRenderer(QByteArray(xml.encode("utf-8")))

    dpr = _device_pixel_ratio()
    physical = max(1, round(size * dpr))
    pixmap = QPixmap(physical, physical)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()

    pixmap.setDevicePixelRatio(dpr)
    return QIcon(pixmap)


def available_names() -> tuple[str, ...]:
    """Lightweight introspection helper, mostly for tests."""
    return tuple(_TEMPLATES.keys())
