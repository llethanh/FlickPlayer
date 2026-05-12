"""On-image A / B markers shown while compare mode is active.

Transparent QWidget sized to the viewer's full rect, painted on top
of :class:`GLViewport`. Renders two small orange letters — one "A",
one "B" — at positions inside the *image* (not the viewport widget),
so they follow pan + zoom and read as part of the picture rather than
floating on the chrome.

Positioning per mode:

* **Vertical wipe**     — A top-left corner of the image,
                          B top-right corner of the image.
* **Horizontal wipe**   — A top-right of the image,
                          B bottom-right of the image.
* **Opacity blend**     — A top-left, B top-right of the image
                          (matches the dropdown order in the band).

Off-screen corners are clamped to the visible viewport so a zoomed-in
view still shows both markers (otherwise B-top-right would drift off
the right edge as soon as the image is bigger than the widget).

Mouse-transparent (same pattern as :class:`BracketsOverlay`) so
right-drag through compare's seam handler keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from img_player.annotate.overlay import image_to_widget
from img_player.compare.state import (
    MODE_HORIZONTAL,
    MODE_OPACITY,
    MODE_VERTICAL,
)

if TYPE_CHECKING:
    from img_player.render.gl_viewport import GLViewport

# Accent orange — same value as ``H.ACCENT`` and the seam line in
# ``compose.py``. Keeps the A/B markers visually tied to the rest of
# the compare UI.
_ACCENT_RGB: tuple[int, int, int] = (232, 144, 28)

# Inset from the image corner toward the centre (image pixels). Small
# enough that the marker reads as "at this corner" without overlapping
# the seam line / blend gradient at the centre.
_CORNER_INSET_PX = 12

# Font size for the letters. Bold + large enough to read at a glance
# but not so large it blocks pixels under it.
_LETTER_PX = 22

# Drop-shadow offset — paints the letter once in dark grey at +1 px,
# then again in orange at 0 px. Gives a thin halo that keeps the
# letter readable on bright frames without resorting to a full
# background capsule.
_SHADOW_OFFSET = 1
_SHADOW_RGB: tuple[int, int, int] = (20, 22, 26)


class CompareLabelsOverlay(QWidget):  # type: ignore[misc]
    """Two minimal "A" / "B" letters anchored to image-space corners."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Same idiom as BracketsOverlay — must not absorb mouse events
        # or the compare-mode right-drag seam gesture installed on the
        # GLViewport (compare_handler._ViewportSeamFilter) stops
        # receiving press events.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

        # Painting is gated by ``_active`` rather than ``setVisible``
        # because the parent ``ViewerWidget`` is the layout owner and
        # we don't want to fight Qt's child-stacking on Windows where
        # ``QOpenGLWidget`` siblings can drop paint events when a
        # nearby widget toggles visibility mid-frame.
        self._active = False
        self._mode = MODE_VERTICAL
        # Seam 0..1 — only consulted in opacity mode where it drives
        # per-letter alpha (A fades out, B fades in as seam rises).
        # Wipe modes ignore this field and paint at full opacity.
        self._seam = 0.5
        # Cached GL viewport pointer (set via :meth:`attach_gl_viewport`)
        # — used both to subscribe to ``transform_changed`` (so the
        # overlay repaints when the user pans / zooms) and to read the
        # current image rect at paint time.
        self._gl: GLViewport | None = None

    # ---- Public API ---------------------------------------------------

    def attach_gl_viewport(self, gl: "GLViewport") -> None:
        """Wire the overlay to a :class:`GLViewport`.

        Subscribing to ``transform_changed`` (pan + zoom) is what
        keeps the markers glued to image corners during interactive
        zoom. Called once from :class:`ViewerWidget` after
        construction.
        """
        if self._gl is gl:
            return
        self._gl = gl
        gl.transform_changed.connect(self._on_gl_transform_changed)

    def set_state(self, active: bool, mode: str, seam: float = 0.5) -> None:
        """Sync paint state + mode + seam from :data:`CompareState`.

        Called by ``compare_handler._sync_compare_labels`` whenever
        the compare state changes (toggle, mode pick, layer swap,
        seam drag). Always queues a repaint when anything user-
        visible changes — covers both the "turn on" / "turn off"
        transitions AND the opacity-mode seam fade.
        """
        active = bool(active)
        seam = max(0.0, min(1.0, float(seam)))
        if (
            self._active == active
            and self._mode == mode
            and self._seam == seam
        ):
            return
        self._active = active
        self._mode = mode
        self._seam = seam
        # Resync geometry against the parent — the overlay is a plain
        # child (not in any layout), so without this it stays at its
        # default 0×0 size and paints nothing. The viewer's
        # ``resizeEvent`` also repositions, but the very first
        # ``set_state`` can race ahead of a layout pass if the user
        # toggles compare straight after a session load.
        parent = self.parent()
        if parent is not None:
            self.setGeometry(parent.rect())
        # Lift to the top of the parent's child stack so the labels
        # paint over any later-added overlay siblings. Cheap no-op
        # when already at the top.
        self.raise_()
        self.update()

    # ---- Paint --------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002
        if not self._active or self._gl is None:
            return
        img_w, img_h = self._gl.image_size()
        if img_w <= 0 or img_h <= 0:
            return
        factor, pan_x, pan_y = self._gl.current_transform()
        if factor <= 0:
            return
        # Map the four image corners into widget space.
        widget_size = (self._gl.width(), self._gl.height())
        img_size = (img_w, img_h)
        pan = (pan_x, pan_y)
        # Insets in image space — keep the letters off the literal
        # corner so they don't bleed into the seam.
        inset = float(_CORNER_INSET_PX)
        # Picks per mode. Each entry is "image-space anchor coord" —
        # later clamped to the visible widget rect so a zoomed-in
        # image doesn't push the markers off-screen.
        anchors: list[tuple[str, tuple[float, float]]]
        if self._mode == MODE_VERTICAL:
            anchors = [
                ("A", (inset, inset)),
                ("B", (img_w - inset, inset)),
            ]
        elif self._mode == MODE_HORIZONTAL:
            anchors = [
                ("A", (img_w - inset, inset)),
                ("B", (img_w - inset, img_h - inset)),
            ]
        elif self._mode == MODE_OPACITY:
            anchors = [
                ("A", (inset, inset)),
                ("B", (img_w - inset, inset)),
            ]
        else:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(_LETTER_PX)
        painter.setFont(font)
        # Per-letter alignment so the anchor lands at the corner of
        # the letter that's closest to the image corner (= the letter
        # grows inward toward the centre, not outward off the edge).
        align_map = {
            ("A", MODE_VERTICAL): Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            ("B", MODE_VERTICAL): Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            ("A", MODE_HORIZONTAL): Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            ("B", MODE_HORIZONTAL): Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            ("A", MODE_OPACITY): Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            ("B", MODE_OPACITY): Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        }

        # Per-letter alpha. Opacity-mode fades A out / B in as the
        # seam moves 0 → 1, so the marker the user is "seeing" stays
        # bright while the other one fades away. Wipe modes both stay
        # opaque — there's no continuous blend, the seam is just a
        # split position.
        if self._mode == MODE_OPACITY:
            alpha_map = {
                "A": 1.0 - self._seam,
                "B": self._seam,
            }
        else:
            alpha_map = {"A": 1.0, "B": 1.0}

        for text, img_xy in anchors:
            alpha = alpha_map.get(text, 1.0)
            if alpha <= 0.0:
                # Fully transparent — skip the paint pass entirely so
                # the shadow doesn't ghost through at alpha 0.
                continue
            wx, wy = image_to_widget(
                image_xy=img_xy,
                widget_size=widget_size,
                img_size=img_size,
                factor=factor,
                pan=pan,
            )
            # Clamp to the visible widget rect so a heavily-zoomed
            # image still shows both markers (otherwise B-right
            # would drift off the right edge as soon as the image
            # extends past the widget). Use the letter-pixel size as
            # a safety margin so the glyph doesn't get cropped.
            margin = float(_LETTER_PX)
            wx = max(margin, min(float(self.width()) - margin, wx))
            wy = max(margin, min(float(self.height()) - margin, wy))
            align = align_map[(text, self._mode)]
            self._draw_letter(painter, QPointF(wx, wy), align, text, alpha)

    # ---- Internals ----------------------------------------------------

    def _draw_letter(
        self,
        painter: QPainter,
        anchor: QPointF,
        alignment: Qt.AlignmentFlag,
        text: str,
        alpha: float = 1.0,
    ) -> None:
        """Paint one letter at ``anchor`` with ``alignment`` controlling
        which corner of the letter's bounding box lands on the anchor.

        Renders the glyph twice: a 1-px dark drop-shadow first, then
        the orange letter on top. The shadow keeps the bare letter
        readable on bright frames where the orange would otherwise
        disappear into a white sky.

        ``alpha`` in [0, 1] scales both the shadow and the foreground
        — used by opacity-mode to fade A out / B in along the seam.
        Wipe modes pass 1.0 and get full-opacity letters.
        """
        metrics = painter.fontMetrics()
        rect = metrics.tightBoundingRect(text)
        w = float(rect.width())
        h = float(rect.height())
        # Map alignment → top-left of the draw box.
        if alignment & Qt.AlignmentFlag.AlignRight:
            x = anchor.x() - w
        elif alignment & Qt.AlignmentFlag.AlignHCenter:
            x = anchor.x() - w / 2.0
        else:  # AlignLeft (default)
            x = anchor.x()
        if alignment & Qt.AlignmentFlag.AlignBottom:
            y = anchor.y()
        elif alignment & Qt.AlignmentFlag.AlignVCenter:
            y = anchor.y() + h / 2.0
        else:  # AlignTop
            y = anchor.y() + h
        a = max(0.0, min(1.0, float(alpha)))
        # Drop-shadow pass (offset +1 px).
        shadow_col = QColor(*_SHADOW_RGB)
        shadow_col.setAlphaF(a)
        painter.setPen(QPen(shadow_col))
        painter.drawText(
            QPointF(x + _SHADOW_OFFSET, y + _SHADOW_OFFSET), text,
        )
        # Foreground pass.
        fg_col = QColor(*_ACCENT_RGB)
        fg_col.setAlphaF(a)
        painter.setPen(QPen(fg_col))
        painter.drawText(QPointF(x, y), text)

    def _on_gl_transform_changed(self) -> None:
        """Repaint when the GL viewport's pan / zoom changes so the
        labels stay glued to the image corners during interactive
        zoom or fit-mode reflow.
        """
        if self._active:
            self.update()
