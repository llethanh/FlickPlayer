"""Composite a :class:`BurninTemplate` onto an RGBA image.

The renderer is the only burnin module that touches Pillow. Its
inputs are pure data — a numpy RGBA array, a frozen
:class:`BurninTemplate`, a :class:`RenderContext` — and its output
is a fresh RGBA array of the same shape. No Qt, no GL, no cache:
the live path, the contact-sheet composer and the export pipeline
all feed the same function.

Pipeline
--------

::

    image_rgba (H, W, 4 uint8)
        │
        │ 1. Convert to PIL.Image (RGBA) once.
        ▼
    canvas
        │
        │ 2. For each enabled bar (top, bottom):
        │    a. Compute bar pixel height (clamped to MIN_BAR_PX).
        │    b. Fill the bar's background (alpha-blended).
        │    c. Resolve every element's tokens, measure widths.
        │    d. Lay out by anchor (left / center / right).
        │    e. Draw text / image / spacer.
        ▼
    PIL.Image → numpy (H, W, 4 uint8)


Sizing rules
------------

* ``BurninBar.height_pct`` × image_height = nominal bar height.
* Bar is clamped to a hard ``MIN_BAR_PX`` so contact-sheet tiles
  stay legible (template authored against 1080p still reads at a
  270-line tile).
* Text ``font_size_pt`` and spacer ``width_px`` are scaled by the
  IMAGE height (``image_h / 1000``), NOT the bar height. That keeps
  resolution scaling (a 12 pt font reads at 12 px on 1080, at 6 px
  on 540) but decouples typography from the bar's ``height_pct`` —
  growing the bar doesn't grow the text inside it.

Font lookup
-----------

Pillow's ``ImageFont.truetype`` doesn't search system font folders
by family name reliably across versions, so we keep a small
``family → file basename`` map covering the families the theme
mentions (Inter, JetBrains Mono) plus Windows defaults (Segoe UI,
Consolas, Arial). On miss we fall back to Pillow's bitmap default
— ugly but never crashes.

Image element lookup
--------------------

``ImageElement.path`` is opened with Pillow at render time; the
result isn't cached here (templates change rarely, and the cache
would have to invalidate on every save anyway). A missing / broken
file is silently skipped — the rest of the burnin renders.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from img_player.burnins.model import (
    MIN_BAR_PX,
    BurninBar,
    BurninTemplate,
    ImageElement,
    SpacerElement,
    TextElement,
)
from img_player.burnins.tokens import RenderContext, resolve

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------- Constants

# Image-height reference for typography + spacer scaling. A 12 pt
# font in a builtin renders at ~12 px on a 1080-line image and at
# ~6 px on a 540-line image (proportional scaling for resolution).
# Critically, this value is the IMAGE height — NOT the bar height —
# so adjusting ``BurninBar.height_pct`` no longer shrinks / grows
# the text inside the bar. The user designs at 1080p once and the
# template behaves the same regardless of bar size on a given image.
#
# 1000 was picked to match the prior 6 %-bar-on-1080 behaviour so
# existing builtins keep rendering identically by default — only
# templates whose bars are NOT at 6 % see the decoupling effect.
_REFERENCE_IMAGE_PX = 1000

# Pillow's default fallback when font lookup fails. Renders as a
# tiny bitmap — readable but obviously a fallback so users notice
# their font isn't installed.
_FALLBACK_FONT = ImageFont.load_default()

# Map a CSS-style font-family name to a likely TTF file basename.
# Covers what the theme references plus Windows defaults. Pillow
# tries each value in order until one resolves; truetype() on
# Windows also searches ``C:\Windows\Fonts`` for bare filenames.
_FONT_FILES: dict[str, tuple[str, ...]] = {
    "Inter":          ("Inter.ttf", "Inter-Regular.ttf", "segoeui.ttf", "arial.ttf"),
    "Inter Bold":     ("Inter-Bold.ttf", "Inter.ttf", "segoeuib.ttf", "arialbd.ttf"),
    "Segoe UI":       ("segoeui.ttf", "arial.ttf"),
    "Segoe UI Bold":  ("segoeuib.ttf", "arialbd.ttf"),
    "JetBrains Mono": ("JetBrainsMono-Regular.ttf", "JetBrainsMono.ttf", "consola.ttf", "cour.ttf"),
    "JetBrains Mono Bold": ("JetBrainsMono-Bold.ttf", "consolab.ttf", "courbd.ttf"),
    "Consolas":       ("consola.ttf", "cour.ttf"),
    "Arial":          ("arial.ttf",),
    "Arial Bold":     ("arialbd.ttf", "arial.ttf"),
}


# ---------------------------------------------------------------------- Color parsing

# ``rgba(r, g, b, a)`` where ``r``/``g``/``b`` are 0-255 integers and
# ``a`` is 0-1 float OR 0-255 integer. Matches the theme.py syntax.
_RGBA_RE = re.compile(
    r"^\s*rgba?\s*\(\s*"
    r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
    r"(?:\s*,\s*([0-9]*\.?[0-9]+))?"
    r"\s*\)\s*$",
)


def _parse_color(color: str) -> tuple[int, int, int, int]:
    """Return ``(r, g, b, a)`` 0-255 integers. Accepts ``#RRGGBB``,
    ``#RRGGBBAA``, and ``rgba(r, g, b, a)`` where ``a`` is 0-1 OR
    0-255. Unknown formats fall back to opaque white so the burnin
    is still visible (and the typo is obvious in the editor)."""
    if not color:
        return (255, 255, 255, 255)
    s = color.strip()
    # Hex form.
    if s.startswith("#"):
        hexpart = s[1:]
        try:
            if len(hexpart) == 6:
                r = int(hexpart[0:2], 16)
                g = int(hexpart[2:4], 16)
                b = int(hexpart[4:6], 16)
                return (r, g, b, 255)
            if len(hexpart) == 8:
                r = int(hexpart[0:2], 16)
                g = int(hexpart[2:4], 16)
                b = int(hexpart[4:6], 16)
                a = int(hexpart[6:8], 16)
                return (r, g, b, a)
        except ValueError:
            pass
    # rgba() form.
    m = _RGBA_RE.match(s)
    if m:
        r = max(0, min(255, int(m.group(1))))
        g = max(0, min(255, int(m.group(2))))
        b = max(0, min(255, int(m.group(3))))
        a_raw = m.group(4)
        if a_raw is None:
            a = 255
        else:
            a_val = float(a_raw)
            # 0-1 float convention (CSS) vs. 0-255 integer convention.
            a = int(round(a_val * 255)) if a_val <= 1.0 else int(round(a_val))
            a = max(0, min(255, a))
        return (r, g, b, a)
    log.debug("Burnin: unrecognised colour %r — falling back to white", color)
    return (255, 255, 255, 255)


# ---------------------------------------------------------------------- Font lookup

# Pillow's ImageFont loader keeps an internal cache, but we still want
# to short-circuit our own family-name → file-path resolution because
# truetype() does disk probing on every miss.
_font_cache: dict[tuple[str, str, int], ImageFont.FreeTypeFont] = {}


def _load_font(family: str, weight: str, size_px: int) -> ImageFont.ImageFont:
    """Return a Pillow font matching ``family`` + ``weight`` at the
    given pixel size. Falls back to the bitmap default rather than
    raising — a missing font shouldn't ever block the burnin."""
    key = (family, weight, size_px)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    lookup = family + (" Bold" if weight == "bold" else "")
    candidates = _FONT_FILES.get(lookup) or _FONT_FILES.get(family) or ()
    # Always try the literal family name first too — covers users who
    # have e.g. ``Inter.ttf`` installed under that exact filename.
    candidates = (family + ".ttf", *candidates)
    for cand in candidates:
        try:
            font = ImageFont.truetype(cand, size_px)
        except OSError:
            continue
        _font_cache[key] = font
        return font
    log.debug("Burnin: no truetype font found for %s %s — using default", family, weight)
    return _FALLBACK_FONT


# ---------------------------------------------------------------------- Element measurement

def _scale_factor(image_h: int) -> float:
    """Map an image pixel height to the typography scale relative to
    the 1000-px reference. A 500-line image renders type at half the
    design size — same scaling as before for the resolution axis,
    but now independent of the bar's height_pct (so a wider bar
    doesn't drag the text larger)."""
    return max(0.4, image_h / _REFERENCE_IMAGE_PX)


def _scaled_font_size(font_size_pt: int, image_h: int) -> int:
    """Pillow's truetype size is pixels, not points — close enough at
    96 dpi. Clamp to a minimum so tiny images don't crash the font
    loader."""
    return max(6, int(round(font_size_pt * _scale_factor(image_h))))


def _measure_text(text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Tight (w, h) of ``text`` rendered with ``font``. Uses textbbox
    so the baseline padding is included (so vertical centering looks
    right). Empty strings measure as ``(0, 0)``.

    Anchor: ``"lt"`` (left-top of the bounding box). This MUST match
    the anchor used in :func:`_draw_element` for the text layer to
    contain every painted pixel — with the default ``"la"`` anchor,
    Pillow puts pixels at y = y0..y1 of the bbox and the layer-only
    sized to ``(w, h)`` would clip descenders at the bottom.
    """
    if not text:
        return (0, 0)
    # ImageDraw.textbbox needs an ImageDraw; we make a 1×1 throwaway.
    probe = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(probe)
    try:
        box = draw.textbbox((0, 0), text, font=font, anchor="lt")
    except Exception:  # noqa: BLE001 — defensive against weird font states
        return (0, 0)
    # With anchor="lt" the bbox origin is the top-left of the painted
    # pixels themselves, so the dims map 1:1 to the layer we'll draw
    # on. box[0]/box[1] should already be ≥ 0; we still subtract for
    # safety against font drivers that pad slightly.
    w = max(0, box[2] - box[0])
    h = max(0, box[3] - box[1])
    return (int(w), int(h))


def _measure_image(path: str, target_h: int) -> tuple[Image.Image | None, int, int]:
    """Load an image element and compute the size it'll render at.

    Returns ``(pil_image, draw_w, draw_h)`` or ``(None, 0, 0)`` if
    the file can't be opened — the rest of the burnin still renders.
    """
    if not path or target_h <= 0:
        return (None, 0, 0)
    try:
        img = Image.open(Path(path)).convert("RGBA")
    except (OSError, ValueError):
        log.debug("Burnin: failed to open image %r", path)
        return (None, 0, 0)
    if img.height <= 0:
        return (None, 0, 0)
    scale = target_h / img.height
    new_w = max(1, int(round(img.width * scale)))
    new_h = max(1, target_h)
    try:
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    except Exception:  # noqa: BLE001
        return (None, 0, 0)
    return (img, new_w, new_h)


# ---------------------------------------------------------------------- Element layout

class _PreparedText:
    __slots__ = ("element", "font", "text", "w", "h", "color")

    def __init__(
        self,
        element: TextElement,
        ctx: RenderContext,
        image_h: int,
    ) -> None:
        self.element = element
        # Scale by IMAGE height — the bar's own height_pct doesn't
        # touch typography (per the user's note: making the bar
        # bigger shouldn't grow the text inside it).
        font_size_px = _scaled_font_size(element.font_size_pt, image_h)
        self.font = _load_font(element.font_family, element.font_weight, font_size_px)
        self.text = resolve(element.text, ctx)
        self.w, self.h = _measure_text(self.text, self.font)
        self.color = _parse_color(element.color)


class _PreparedImage:
    __slots__ = ("element", "pil", "w", "h")

    def __init__(self, element: ImageElement, bar_px: int) -> None:
        self.element = element
        # Image height is a fraction of the bar height.
        target_h = max(1, int(round(bar_px * element.height_pct)))
        self.pil, self.w, self.h = _measure_image(element.path, target_h)


class _PreparedSpacer:
    __slots__ = ("element", "w", "h")

    def __init__(self, element: SpacerElement, image_h: int) -> None:
        self.element = element
        # Spacer width scales with the IMAGE height (same axis as
        # font size) — keeps gaps proportional across resolutions but
        # decouples them from the bar's height adjustment.
        self.w = max(0, int(round(element.width_px * _scale_factor(image_h))))
        self.h = 0


def _prepare_elements(
    bar: BurninBar, ctx: RenderContext, bar_px: int, image_h: int,
) -> list[object]:
    """Build prepared elements. Text + spacer scale by the IMAGE
    height (independent of bar size); the image element scales by
    bar height (so a logo fills the bar like a label would)."""
    out: list[object] = []
    for elem in bar.elements:
        if isinstance(elem, TextElement):
            out.append(_PreparedText(elem, ctx, image_h))
        elif isinstance(elem, ImageElement):
            out.append(_PreparedImage(elem, bar_px))
        elif isinstance(elem, SpacerElement):
            out.append(_PreparedSpacer(elem, image_h))
    return out


# ---------------------------------------------------------------------- Bar drawing

def _draw_element(
    draw: ImageDraw.ImageDraw,
    canvas: Image.Image,
    prepared: object,
    x: int,
    y: int,
    bar_top: int,
    bar_px: int,
) -> None:
    """Paint a single prepared element. ``(x, y)`` is the element's
    top-left in canvas coordinates; ``bar_top`` / ``bar_px`` describe
    the bar so we can vertically centre."""
    if isinstance(prepared, _PreparedText):
        # Vertical centre of the bar, plus the element's offset_y.
        text_y = bar_top + (bar_px - prepared.h) // 2 + int(round(prepared.element.offset_y))
        if prepared.w <= 0 or prepared.h <= 0:
            return
        # Important: ``ImageDraw.text`` with an RGBA ``fill`` whose
        # alpha is < 255 OVERWRITES the canvas pixels — it does NOT
        # alpha-composite. So a text colour with alpha=0 would punch
        # transparent holes through the bar background (the user
        # reported this as "text becomes dark"). Render onto a
        # small transparent layer first, then alpha-composite the
        # layer onto the canvas: the bar bg shows through any
        # text-shaped pixel where the fill's alpha is below 255,
        # which is the behaviour the editor's opacity slider wants.
        try:
            # Anchor MUST be "lt" here so it matches _measure_text:
            # the layer is sized to (w, h) and pixels need to land in
            # (0, 0)..(w, h) without bleeding below — otherwise
            # descenders (g, p, y) get clipped at the bottom of the
            # layer.
            text_layer = Image.new(
                "RGBA", (prepared.w, prepared.h), (0, 0, 0, 0),
            )
            ImageDraw.Draw(text_layer).text(
                (0, 0), prepared.text,
                font=prepared.font, fill=prepared.color,
                anchor="lt",
            )
            canvas.alpha_composite(text_layer, dest=(x, text_y))
        except Exception:  # noqa: BLE001
            log.debug("Burnin: text draw failed for %r", prepared.text)
    elif isinstance(prepared, _PreparedImage) and prepared.pil is not None:
        img_y = bar_top + (bar_px - prepared.h) // 2 + int(round(prepared.element.offset_y))
        try:
            canvas.alpha_composite(prepared.pil, dest=(x, img_y))
        except Exception:  # noqa: BLE001
            log.debug("Burnin: image draw failed for %r", prepared.element.path)
    # Spacers don't draw — they just consumed width during layout.


def _record_rect(
    out: dict | None,
    bar_id: str,
    elem_idx: int,
    x: int, y: int, w: int, h: int,
) -> None:
    """Helper — populate the optional ``out_element_rects`` dict with
    the bounding box of an element we just drew, keyed by
    ``(bar_id, elem_idx)``. The editor uses this for click-to-select
    hit testing; non-editor callers pass ``None`` and pay nothing."""
    if out is None:
        return
    out[(bar_id, elem_idx)] = (int(x), int(y), int(w), int(h))


def _draw_bar(
    canvas: Image.Image,
    bar: BurninBar,
    ctx: RenderContext,
    bar_top: int,
    bar_px: int,
    image_w: int,
    image_h: int,
    *,
    bar_id: str | None = None,
    out_element_rects: dict | None = None,
) -> None:
    """Composite a single bar (background + every element) onto the
    canvas. ``bar_top`` is the y of the bar's top edge in canvas
    coordinates.

    When ``out_element_rects`` is provided, the function populates it
    with one ``(bar_id, original_element_index)`` → ``(x, y, w, h)``
    entry per element drawn — used by the editor's preview for
    click-to-select hit testing. ``bar_id`` is the same string the
    editor expects ("top" / "bottom")."""
    if not bar.enabled or bar_px <= 0:
        return

    # Background: a translucent rect over the bar's full width. Use
    # alpha_composite so the bg colour's alpha actually blends
    # rather than overwriting.
    bg_color = _parse_color(bar.bg_color)
    if bg_color[3] > 0:
        bg = Image.new("RGBA", (image_w, bar_px), bg_color)
        canvas.alpha_composite(bg, dest=(0, bar_top))

    prepared = _prepare_elements(bar, ctx, bar_px, image_h)
    if not prepared:
        return

    draw = ImageDraw.Draw(canvas)

    # Keep a parallel list of "original index" so the recorded rects
    # match the element order the editor sees in its tree.
    indexed = list(enumerate(prepared))

    # Split by anchor — preserve declaration order within each
    # anchor so the template author's intent is honoured.
    left = [(i, p) for i, p in indexed if getattr(p, "element").anchor == "left"]
    center = [(i, p) for i, p in indexed if getattr(p, "element").anchor == "center"]
    right = [(i, p) for i, p in indexed if getattr(p, "element").anchor == "right"]

    def _y_for(prep) -> int:  # type: ignore[no-untyped-def]
        # Centred in the bar, offset by the element's own offset_y.
        h_elem = getattr(prep, "h", 0)
        off_y = int(round(getattr(prep.element, "offset_y", 0)))
        return bar_top + (bar_px - h_elem) // 2 + off_y

    # Left anchor: first element's ``offset_x`` is the gap from the
    # left edge; subsequent elements' ``offset_x`` is the gap from
    # the previous element. A running cursor walks left-to-right.
    cursor = 0
    for idx, p in left:
        elem = getattr(p, "element")
        if cursor == 0:
            cursor = int(round(elem.offset_x))
        else:
            cursor += int(round(elem.offset_x))
        _draw_element(draw, canvas, p, cursor, 0, bar_top, bar_px)
        if bar_id is not None:
            _record_rect(
                out_element_rects, bar_id, idx,
                cursor, _y_for(p), getattr(p, "w", 0), getattr(p, "h", 0) or bar_px,
            )
        cursor += getattr(p, "w", 0)

    # Right anchor: lay right-to-left from the right edge. The
    # template's ``offset_x`` is typically negative (e.g. -16) — we
    # use its absolute value as a gap from the edge.
    cursor = image_w
    for idx, p in right:
        elem = getattr(p, "element")
        if cursor == image_w:
            cursor = image_w + int(round(elem.offset_x))
        else:
            cursor += int(round(elem.offset_x))
        w = getattr(p, "w", 0)
        cursor -= w
        _draw_element(draw, canvas, p, cursor, 0, bar_top, bar_px)
        if bar_id is not None:
            _record_rect(
                out_element_rects, bar_id, idx,
                cursor, _y_for(p), w, getattr(p, "h", 0) or bar_px,
            )

    # Center anchor: measure the total group width (incl. internal
    # cumulative offsets), centre it, then lay left-to-right.
    total_w = sum(getattr(p, "w", 0) for _i, p in center) + sum(
        int(round(getattr(p, "element").offset_x)) for _i, p in center[1:]
    )
    cursor = (image_w - total_w) // 2
    if center:
        cursor += int(round(getattr(center[0][1], "element").offset_x))
    for n, (idx, p) in enumerate(center):
        if n > 0:
            cursor += int(round(getattr(p, "element").offset_x))
        _draw_element(draw, canvas, p, cursor, 0, bar_top, bar_px)
        if bar_id is not None:
            _record_rect(
                out_element_rects, bar_id, idx,
                cursor, _y_for(p), getattr(p, "w", 0), getattr(p, "h", 0) or bar_px,
            )
        cursor += getattr(p, "w", 0)


# ---------------------------------------------------------------------- Public API

def render_burnin(
    image_rgba: np.ndarray,
    template: BurninTemplate,
    context: RenderContext,
    *,
    out_element_rects: dict | None = None,
) -> np.ndarray:
    """Composite ``template`` onto ``image_rgba`` and return the new
    array.

    Parameters
    ----------
    image_rgba : ndarray
        Shape ``(H, W, 4)`` uint8. The function operates on a copy
        so the caller's buffer is not mutated.
    template : BurninTemplate
        The template to draw.
    context : RenderContext
        Live values for token substitution + sizing.

    Returns
    -------
    ndarray
        Shape ``(H, W, 4)`` uint8 with the burnin baked in. Same
        shape as the input — bars overlay the image, they don't add
        height. If the input has neither bar enabled or the image is
        too small for a single bar, the input is returned unmodified
        (still a copy — callers can freely mutate).
    """
    if image_rgba.ndim != 3 or image_rgba.shape[2] != 4:
        # Defensive: we only handle RGBA. Anything else is a bug at
        # the call site; copy and return so playback keeps moving.
        return image_rgba.copy()

    # Pillow expects uint8 RGBA; everything in the rendering path
    # (compositor, contact-sheet composer, export bake) already
    # provides that, so coerce only as a guard against future drift.
    if image_rgba.dtype != np.uint8:
        image_rgba = image_rgba.astype(np.uint8, copy=False)

    h, w, _ = image_rgba.shape
    if h <= 0 or w <= 0:
        return image_rgba.copy()

    top_px = (
        max(MIN_BAR_PX, int(round(h * template.top_bar.height_pct)))
        if template.top_bar.enabled else 0
    )
    bottom_px = (
        max(MIN_BAR_PX, int(round(h * template.bottom_bar.height_pct)))
        if template.bottom_bar.enabled else 0
    )

    # Hard floor: if the image is so short that the bars would
    # overlap, skip everything — better than rendering on top of
    # itself.
    if top_px + bottom_px >= h:
        return image_rgba.copy()

    canvas = Image.fromarray(image_rgba, mode="RGBA")

    if template.top_bar.enabled:
        _draw_bar(
            canvas, template.top_bar, context, 0, top_px, w, h,
            bar_id="top", out_element_rects=out_element_rects,
        )

    if template.bottom_bar.enabled:
        _draw_bar(
            canvas, template.bottom_bar, context,
            h - bottom_px, bottom_px, w, h,
            bar_id="bottom", out_element_rects=out_element_rects,
        )

    return np.array(canvas, dtype=np.uint8)
