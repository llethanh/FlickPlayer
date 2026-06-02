"""Container around the GL viewport.

Stacks decorative / interactive overlay widgets on top of the GL
viewport via ``QStackedLayout`` in ``StackAll`` mode. The overlay
slot starts with the corner :class:`BracketsOverlay` from the design
charter; it's the same architecture annotation tools (V3) will plug
into without touching GL code.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedLayout, QWidget

from img_player.render.gl_viewport import GLViewport
from img_player.ui.brackets_overlay import BracketsOverlay
from img_player.ui.compare_labels_overlay import CompareLabelsOverlay
from img_player.ui.drop_zone import (
    REPLACE_ACCENT,
    DropOverlay,
    install_file_drop_zone,
)


class ViewerWidget(QWidget):  # type: ignore[misc]
    """GL viewport + decorative brackets overlay (and future annotation slot)."""

    # File(s) / folder(s) dropped on the viewer — the user wants to
    # replace the currently loaded sequence. Same destination as
    # File → Open. Carries a list because a single drop can include
    # multiple folders / files; the picker resolves the choice.
    replace_requested = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._gl = GLViewport()
        # Decorative L-brackets in the four corners. Transparent to
        # mouse events — clicks fall through to the GL widget so drag
        # & drop of sequences keeps working.
        self._overlay = BracketsOverlay(self)

        layout = QStackedLayout(self)
        layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._gl)
        layout.addWidget(self._overlay)

        # A / B markers shown only while compare mode is active. Kept
        # OUT of the QStackedLayout — on Windows the QOpenGLWidget
        # composes via native surface and a stacked sibling can race
        # the GL paint, producing dropped first-paints of the overlay.
        # The same pattern as the header strip (child of ``self``,
        # ``raise_()``d after resize) sidesteps it.
        self._compare_labels = CompareLabelsOverlay(self)
        # Hand the overlay a handle on the GL viewport so it can read
        # the live image-to-widget transform AND subscribe to
        # ``transform_changed`` for pan / zoom repaint.
        self._compare_labels.attach_gl_viewport(self._gl)
        self._compare_labels.raise_()

        # Drop zone with a "REPLACE" overlay shown during drag-over.
        # Sits as a child of ``self`` (not in the stacked layout) so
        # we can ``raise_()`` it to the absolute top during a drag —
        # the brackets overlay already lives in the stack and would
        # otherwise paint on top of the drop hint.
        self._drop_overlay = DropOverlay("REPLACE", REPLACE_ACCENT, self)
        install_file_drop_zone(
            self, self._drop_overlay,
            lambda paths: self.replace_requested.emit(paths),
        )

        # Header info strip — brief §2. The orange cartouche with
        # sequence name / resolution / fps / Layer / Frame. Floating
        # overlay flush with the bottom edge of the viewer (the user
        # asked for it to overlay inside the display area, not take
        # layout space below it). Built as a child of self so the
        # parent-relative absolute positioning naturally tracks
        # viewer resizes. Hidden by default; surfaces when
        # ``set_visible_for_sequence(True)`` fires from
        # ``MainWindow.update_sequence_info``.
        from img_player.ui.header_strip import HeaderInfoStrip  # noqa: PLC0415
        self._header_strip = HeaderInfoStrip(self)
        self._header_strip.raise_()

        # Burnin overlay — paints info bars (sequence name, frame
        # counter, user, date, logos…) over the GL viewport using a
        # CPU-rendered RGBA pixmap. Hidden by default; the View menu
        # / Ctrl+B toggles it. Sits BELOW the header strip and the
        # compare labels in z-order so those review-mode HUDs stay
        # readable when the user has burnins on. Transparent to
        # mouse events — clicks fall through to the GL widget.
        from img_player.ui.burnin_overlay import BurninOverlay  # noqa: PLC0415
        self._burnin_overlay = BurninOverlay(self)
        # The overlay needs a handle on the GL widget so it can read
        # the live image rect (image size × zoom × pan) and follow
        # the picture on pan / zoom — the burnin sits ON the image,
        # not on the widget.
        self._burnin_overlay.attach_gl_viewport(self._gl)
        # Below the header strip + compare labels but above the GL
        # widget. We raise the strip + compare labels AFTER to
        # restore that order.
        self._burnin_overlay.raise_()
        self._header_strip.raise_()
        self._compare_labels.raise_()

    @property
    def gl(self) -> GLViewport:
        return self._gl

    @property
    def overlay(self) -> BracketsOverlay:
        return self._overlay

    @property
    def compare_labels(self) -> CompareLabelsOverlay:
        return self._compare_labels

    @property
    def header_strip(self):  # type: ignore[no-untyped-def]
        """Floating header cartouche (sequence name / resolution / fps
        / Layer / Frame), pinned to the bottom edge of the viewer.
        See :mod:`img_player.ui.header_strip`."""
        return self._header_strip

    @property
    def burnin_overlay(self):  # type: ignore[no-untyped-def]
        """Active burnin overlay — full-widget translucent layer.
        See :mod:`img_player.ui.burnin_overlay`."""
        return self._burnin_overlay

    def _reposition_header_strip(self) -> None:
        """Pin the header info strip to the bottom edge of the viewer.
        The strip overlays the bottom of the image (the user prefers
        this to taking layout space below the viewer). Visible state
        is controlled by the caller via ``set_visible_for_sequence``."""
        h = self._header_strip.height()
        self._header_strip.setGeometry(0, self.height() - h, self.width(), h)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        # Keep the drop-overlay sized with the widget while it's
        # visible (unusual case — drag-over during a window resize —
        # but trivial to support).
        if self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.rect())
        self._reposition_header_strip()
        # Burnin overlay also tracks the viewer rect — burnins
        # anchor to the widget edges (not the letterboxed image rect)
        # so they stay visible regardless of pan / zoom.
        self._burnin_overlay.setGeometry(self.rect())
        # Compare-mode A/B overlay tracks the viewer rect (it's a
        # plain child, not in the QStackedLayout). Without this it'd
        # stay at its initial 0×0 size and paint nothing.
        self._compare_labels.setGeometry(self.rect())
        self._compare_labels.raise_()
