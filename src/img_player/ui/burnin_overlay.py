"""Overlay widget that paints the active burnin over the GL viewport.

Why an overlay, not a baked-in texture
--------------------------------------

The GL viewport runs OCIO color transform in a fragment shader; the
RGBA texture it samples is in the SOURCE colorspace. A burnin baked
into that texture would carry display-referred colours
(``#FFE5C0`` cream, accent orange, …) through the OCIO pipeline
and come out wrong — what the user picked in the editor would NOT
be what the screen shows.

A Qt overlay drawn on top of the GL widget paints natively in
display space, so the colours land exactly. The pixmap it shows is
produced by :func:`img_player.burnins.renderer.render_burnin`, the
same renderer the contact-sheet composer and the export bake use —
that guarantees WYSIWYG across live / CS / export.

Re-render cadence
-----------------

Pillow text rasterisation is a few ms; running it once per played
frame would be wasteful when the only token changing is
``{frame}``. The overlay memoises the rendered pixmap on a
*signature* derived from the template + context, and only rebuilds
when the signature changes — so a 24 fps playback rebuilds 24 times
a second, but a parked playhead repaints from cache.

The signature deliberately includes ``frame`` so the counter
updates per tick. Tokens that don't change frame-to-frame
(``user``, ``date``, ``resolution``) get the cache hit anyway
because they don't move.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from img_player.burnins.model import BurninTemplate
from img_player.burnins.renderer import render_burnin
from img_player.burnins.tokens import RenderContext

if TYPE_CHECKING:
    from img_player.render.gl_viewport import GLViewport

log = logging.getLogger(__name__)


class BurninOverlay(QWidget):  # type: ignore[misc]
    """Translucent QWidget overlay that paints the active burnin.

    The overlay sizes itself to the parent (viewer) widget but the
    burnin is rendered onto **only the image rect** (i.e. the
    letterboxed area where the GL viewport draws the picture). When
    the user pans / zooms, the burnin moves with the image — bars
    sit flush against the top / bottom edges of the picture, not the
    widget. That's what the user asked for, and what every other VFX
    dailies player does.

    The widget is **transparent to mouse events** so drag-to-scrub
    and right-click context menus on the viewport keep working.

    Public API:
      * :meth:`attach_gl_viewport` — hand the overlay a handle on the
        GL viewport so it can read ``image_size`` /
        ``current_transform`` and subscribe to ``transform_changed``.
      * :meth:`set_enabled` — show / hide.
      * :meth:`set_template` — swap to a different template.
      * :meth:`set_context` — push a fresh :class:`RenderContext`.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Clicks fall through to the GL viewport below.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # We paint our own pixmap; let the widget background stay clear.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Hidden by default — the View menu / Ctrl+B turns the burnin on.
        self.setVisible(False)

        self._enabled = False
        self._template: BurninTemplate | None = None
        self._context = RenderContext()
        # GL viewport handle — set via :meth:`attach_gl_viewport`. We
        # query it on every paint for the live image rect, and
        # subscribe to ``transform_changed`` so pan / zoom invalidate
        # the cache.
        self._gl: GLViewport | None = None

        # Cache: pixmap rendered for the current
        # (template, context, image_rect) tuple. Image rect is in
        # the signature so cache invalidates on pan / zoom.
        self._cached_pixmap: QPixmap | None = None
        self._cached_signature: tuple[Any, ...] | None = None
        # Last known image rect — also where the pixmap should paint.
        self._image_rect: tuple[int, int, int, int] = (0, 0, 0, 0)

    # ---- Public API -------------------------------------------------------

    def attach_gl_viewport(self, gl: "GLViewport") -> None:
        """Wire the overlay to the :class:`GLViewport` it sits on top of.

        We need the GL widget for two reasons:

        * Read the current **image rect** (image size × zoom factor +
          pan offsets) on every paint so the burnin bars hug the
          image's top / bottom edges rather than the widget's.
        * Subscribe to ``transform_changed`` (pan, zoom, fit-mode
          reflow) so the cached pixmap invalidates the moment the
          image rect moves — otherwise a pan would slide the image
          out from under a stale burnin.
        """
        if self._gl is gl:
            return
        self._gl = gl
        gl.transform_changed.connect(self._on_gl_transform_changed)

    def set_enabled(self, on: bool) -> None:
        """Show / hide the overlay. A subsequent template / context
        push still updates internal state, so toggling off then on
        again paints the current frame immediately."""
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        self.setVisible(on and self._template is not None)
        if on:
            self.update()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_template(self, template: BurninTemplate | None) -> None:
        """Swap to a new template. ``None`` clears the overlay (the
        toggle stays in whatever state it was — re-setting a template
        later resumes painting)."""
        if template is self._template:
            return
        self._template = template
        # Force a rebuild on the next paint by clearing the cache.
        self._cached_pixmap = None
        self._cached_signature = None
        self.setVisible(self._enabled and template is not None)
        if self._enabled:
            self.update()

    def template(self) -> BurninTemplate | None:
        return self._template

    def set_context(self, context: RenderContext) -> None:
        """Push a fresh :class:`RenderContext`. The pixmap is rebuilt
        on the next paint if the relevant signature changed."""
        if context == self._context:
            return
        self._context = context
        if self._enabled and self._template is not None:
            self.update()

    # ---- Sizing -----------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Invalidate the pixmap when the widget resizes — the image
        rect almost certainly changed too (fit-mode reflow)."""
        super().resizeEvent(event)
        self._cached_pixmap = None
        self._cached_signature = None

    # ---- Painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if not self._enabled or self._template is None:
            return
        if self.width() <= 0 or self.height() <= 0:
            return

        pixmap = self._pixmap_for_current_state()
        if pixmap is None or pixmap.isNull():
            return

        # Paint at the image rect's top-left — the rect is in widget
        # coords; the pixmap was rendered at the rect's dimensions, so
        # bars sit flush against the image's top / bottom edges, not
        # the widget's. Pan / zoom slide the pixmap with the image.
        left, top, _, _ = self._image_rect
        painter = QPainter(self)
        try:
            painter.drawPixmap(left, top, pixmap)
        finally:
            painter.end()

    # ---- Internals --------------------------------------------------------

    def _on_gl_transform_changed(self) -> None:
        """Repaint when the GL viewport's pan / zoom (or fit-mode
        reflow) changes the image rect. The next paint invalidates
        the cache via the signature check."""
        if self._enabled and self._template is not None:
            self.update()

    def _compute_image_rect(self) -> tuple[int, int, int, int]:
        """Return ``(left, top, width, height)`` of the GL viewport's
        drawn image area, in this overlay's local coordinates (the
        overlay is sized to the viewer, which matches the GL widget,
        so widget coords are local coords).

        Returns ``(0, 0, widget_w, widget_h)`` as a sensible fallback
        when no GL viewport is attached or no image is loaded —
        better than ``(0, 0, 0, 0)`` because that would hide the burnin
        entirely in unit tests where the GL handle isn't wired.
        """
        if self._gl is None:
            return (0, 0, self.width(), self.height())
        img_w, img_h = self._gl.image_size()
        if img_w <= 0 or img_h <= 0:
            return (0, 0, self.width(), self.height())
        factor, pan_x, pan_y = self._gl.current_transform()
        if factor <= 0:
            return (0, 0, self.width(), self.height())
        drawn_w = img_w * factor
        drawn_h = img_h * factor
        # Same centring + pan formula the GL viewport itself uses (see
        # ``GLViewport._cs_tile_at`` for the canonical version).
        left = (self.width() - drawn_w) / 2 + pan_x
        top = (self.height() - drawn_h) / 2 + pan_y
        return (
            int(round(left)), int(round(top)),
            max(1, int(round(drawn_w))),
            max(1, int(round(drawn_h))),
        )

    def _signature(self) -> tuple[Any, ...]:
        """Cache key — includes template identity, render context,
        the image rect (captures widget size, zoom factor and pan in
        one 4-tuple), device pixel ratio (drives the supersampling
        factor), and source image size (feeds the typography scale).
        Any change here invalidates the cache → next paint rebuilds
        the pixmap at the new resolution."""
        return (
            self._template,
            self._context,
            self._image_rect,
            self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0,
            self._gl.image_size() if self._gl is not None else (0, 0),
        )

    def _pixmap_for_current_state(self) -> QPixmap | None:
        """Return a QPixmap of the rendered burnin, building (and
        caching) it if the signature has changed.

        Quality is the whole point of this method:

        * **HiDPI** — we rasterise at ``image_rect_size *
          devicePixelRatioF()`` so a 125 % / 150 % / 200 % display
          gets crisp glyphs instead of a bilinear-upscaled, blurry
          pixmap. The resulting :class:`QPixmap` carries
          ``setDevicePixelRatio(dpr)`` so Qt paints it back at the
          logical rect size we asked for.
        * **Resolution scaling** — when the widget is showing the
          image well below 100 % zoom (e.g. fit-to-window on a 4K
          source in a 1080p widget), the image rect is small and
          typography would render at a tiny pixel count. We
          oversample the renderer with ``max(rect_size, source_size)
          × dpr`` so the font is drawn at the source's native
          resolution at minimum, then Qt downscales to the rect for
          display. Net effect: text looks the same regardless of
          viewport zoom — what the editor previewed is what plays.
        """
        rect = self._compute_image_rect()
        self._image_rect = rect
        sig = self._signature()
        if (
            self._cached_pixmap is not None
            and self._cached_signature == sig
        ):
            return self._cached_pixmap

        if self._template is None:
            return None

        _, _, rect_w, rect_h = rect
        if rect_w <= 0 or rect_h <= 0:
            return None

        # Render resolution = max(displayed rect, source image) ×
        # device pixel ratio. The ``max`` keeps quality up when the
        # viewport is zoomed out (small rect_h → small font). The
        # DPR factor compensates for HiDPI scaling. A hard ceiling
        # at 4× the rect dimensions caps work on absurdly large
        # sources (16K plates rendered onto a 1k viewport).
        dpr = float(
            self.devicePixelRatioF()
            if hasattr(self, "devicePixelRatioF") else 1.0,
        )
        if dpr <= 0:
            dpr = 1.0
        src_w, src_h = (
            self._gl.image_size()
            if self._gl is not None else (0, 0)
        )
        # Prefer the source height because the renderer scales
        # typography by image height (see ``_REFERENCE_IMAGE_PX``
        # in burnins/renderer). Width matches the proportional scale.
        if src_h > 0 and src_w > 0:
            scale = max(1.0, src_h / max(1, rect_h))
        else:
            scale = 1.0
        # Cap the scale so a 16K plate displayed at 1080p doesn't
        # spend 15× the budget on burnin pixels we'll throw away
        # downscaling.
        scale = min(scale, 4.0)
        render_w = max(1, int(round(rect_w * scale * dpr)))
        render_h = max(1, int(round(rect_h * scale * dpr)))

        # Render onto a fully-transparent buffer the size of the
        # oversampled image rect. The renderer overlays bars at top /
        # bottom of the buffer → bars sit at the top / bottom of the
        # image when the pixmap is painted at the rect's offset.
        canvas = np.zeros((render_h, render_w, 4), dtype=np.uint8)
        try:
            painted = render_burnin(canvas, self._template, self._context)
        except Exception:  # noqa: BLE001 — never crash playback on a burnin error
            log.exception("Burnin overlay render failed — disabling for this frame.")
            self._cached_pixmap = QPixmap()
            self._cached_signature = sig
            return self._cached_pixmap

        # numpy RGBA → QImage → QPixmap. ``Format_RGBA8888`` matches
        # our numpy memory layout exactly so no per-pixel conversion
        # is needed. ``copy()`` is needed because QImage doesn't own
        # the numpy buffer and a numpy GC would dangle the QImage.
        qimg = QImage(
            painted.data, render_w, render_h, painted.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy()
        pixmap = QPixmap.fromImage(qimg)
        # Tell Qt the pixmap is "logical-rect-size × dpr" pixels —
        # ``drawPixmap(left, top, pixmap)`` then paints at the
        # ``rect_w × rect_h`` logical extent, downsampling our
        # oversampled raster on the GPU. For DPR=1 + scale=1 this
        # reduces to the previous behaviour.
        effective_dpr = scale * dpr
        if effective_dpr > 0:
            pixmap.setDevicePixelRatio(effective_dpr)
        self._cached_pixmap = pixmap
        self._cached_signature = sig
        return pixmap
