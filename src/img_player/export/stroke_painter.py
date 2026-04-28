"""Pure painter helpers for baking strokes into a buffer.

The live ``AnnotationOverlay`` paints strokes via QPainter onto a
QWidget. The export needs the same drawing logic onto a
``QPainter`` mounted on an offscreen ``QImage``. Rather than
duplicate the Bézier-smoothing path, we extract the rendering
geometry into a free function used by **both**:

* :meth:`AnnotationOverlay._draw_stroke` (existing paint path)
* :func:`paint_strokes_into_image` (export bake, this module)

By keeping it pure (just ``QPainter`` + numbers in, draws out), no
QWidget needed, the same code runs in the offscreen export thread.

Coordinate system contract
--------------------------
Strokes carry image-space points (px). When painting onto an
offscreen image the size of the source frame, the natural transform
is ``factor = 1.0`` and ``pan = (0, 0)`` — so image coords map 1:1
onto image-space pixel positions in the QImage. The same module
works for the live overlay by passing the viewport's runtime
``factor`` / ``pan`` instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from img_player.annotate.overlay import image_to_widget
from img_player.annotate.stroke import Stroke


def paint_strokes(
    painter: QPainter,
    strokes: Iterable[Stroke],
    *,
    widget_size: tuple[int, int],
    img_size: tuple[int, int],
    factor: float = 1.0,
    pan: tuple[float, float] = (0.0, 0.0),
    alpha: float = 1.0,
) -> None:
    """Paint a sequence of strokes onto ``painter``.

    Mirrors :meth:`AnnotationOverlay._draw_stroke` algorithmically.
    The midpoint-quadratic smoothing is the same as the live overlay
    so the bake matches what the user saw on screen.

    Caller is responsible for opening + closing the painter (we don't
    own its lifecycle — both the widget paintEvent and the export
    bake have their own painter management).
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for stroke in strokes:
        _paint_one(
            painter,
            stroke.points,
            stroke.color,
            stroke.size,
            widget_size,
            img_size,
            factor,
            pan,
            alpha=alpha,
        )


def _paint_one(
    painter: QPainter,
    points: tuple[tuple[float, float], ...],
    color: str,
    size: float,
    widget_size: tuple[int, int],
    img_size: tuple[int, int],
    factor: float,
    pan: tuple[float, float],
    *,
    alpha: float,
) -> None:
    pen_color = QColor(color)
    if alpha != 1.0:
        pen_color.setAlphaF(max(0.0, min(1.0, alpha)) * pen_color.alphaF())
    pen = QPen(pen_color)
    pen.setWidthF(max(1.0, size * factor))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    widget_pts = [
        image_to_widget(
            image_xy=p,
            widget_size=widget_size,
            img_size=img_size,
            factor=factor,
            pan=pan,
        )
        for p in points
    ]

    path = QPainterPath()
    path.moveTo(*widget_pts[0])

    if len(widget_pts) == 1:
        path.lineTo(*widget_pts[0])
    elif len(widget_pts) == 2:
        path.lineTo(*widget_pts[1])
    else:
        for i in range(1, len(widget_pts) - 1):
            mid_x = (widget_pts[i][0] + widget_pts[i + 1][0]) / 2.0
            mid_y = (widget_pts[i][1] + widget_pts[i + 1][1]) / 2.0
            path.quadTo(*widget_pts[i], mid_x, mid_y)
        path.lineTo(*widget_pts[-1])

    painter.drawPath(path)
