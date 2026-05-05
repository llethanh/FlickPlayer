"""Bottom-anchored info band over the viewer.

Semi-transparent orange strip showing image dimensions, fps, the
local layer-relative frame, and the global timeline-relative frame.
Inspired by PDPlayer's bottom HUD. Lives inside the display area
(child of :class:`ViewerWidget`) so it sits *above* the timeline,
not inside it.

The band is mouse-transparent — clicks fall through to the GL
viewport so drag-scrub keeps working when the band is shown.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from img_player.ui.frame_display import _frame_to_timecode
from img_player.ui.theme import H

DisplayMode = str  # "frames" | "tc" — same vocabulary as Timeline / TransportBar.


class InfoBand(QWidget):  # type: ignore[misc]
    """Orange bottom HUD — four readouts separated by thin dividers."""

    HEIGHT = 22  # px — kept compact so it doesn't crop the image too much

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Required for stylesheet ``background:`` to actually paint
        # on a custom QWidget subclass. Without this the rule below
        # only colours the QLabel children and the band itself stays
        # transparent — image visible underneath in patches.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Background: ACCENT (#E8901C) at ~55% alpha. The dim text
        # then reads cleanly without fighting the image behind.
        self.setStyleSheet(
            "InfoBand {"
            "  background: rgba(232, 144, 28, 140);"
            "}"
            "QLabel {"
            "  color: #FFE5C0;"
            "  font-family: 'Consolas', 'Menlo', 'JetBrains Mono', monospace;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "  background: transparent;"
            "}"
            "QLabel#sep {"
            "  color: rgba(255, 229, 192, 110);"
            "  font-weight: 400;"
            "}"
        )
        self.setFixedHeight(self.HEIGHT)

        self._size_lbl = QLabel("—", self)
        self._fps_lbl = QLabel("—", self)
        self._local_lbl = QLabel("—", self)
        self._global_lbl = QLabel("—", self)
        # Cached state — needed when ``set_display_mode`` flips between
        # frame numbers and timecode and we want to re-render without
        # waiting for the next frame_changed tick.
        self._mode: DisplayMode = "frames"
        self._fps: float = 24.0
        self._local_cur: int | None = None
        self._local_max: int | None = None
        self._global_cur: int | None = None
        self._global_max: int | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)
        lay.addWidget(self._size_lbl)
        lay.addWidget(self._sep())
        lay.addWidget(self._fps_lbl)
        lay.addWidget(self._sep())
        lay.addWidget(self._local_lbl)
        lay.addWidget(self._sep())
        lay.addWidget(self._global_lbl)
        lay.addStretch(1)

    @staticmethod
    def _sep() -> QLabel:
        s = QLabel("│")
        s.setObjectName("sep")
        return s

    def set_image_size(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            self._size_lbl.setText("— × —")
        else:
            self._size_lbl.setText(f"{width} × {height}")

    def set_fps(self, fps: float | None) -> None:
        if fps is None or fps <= 0:
            self._fps_lbl.setText("— fps")
        else:
            self._fps_lbl.setText(f"{fps:.3f} fps")
        # The timecode formatter needs the fps too — keep it cached
        # so a TC re-render doesn't have to chase the controller.
        if fps is not None and fps > 0:
            self._fps = float(fps)
            if self._mode == "tc":
                # Repaint with new fps mapping without waiting for the
                # next playhead tick.
                self._render_local()
                self._render_global()

    def set_display_mode(self, mode: DisplayMode) -> None:
        """Switch between frame numbers and timecode for the layer /
        frame readouts. Same vocabulary as Timeline / TransportBar."""
        if mode == self._mode:
            return
        self._mode = mode
        self._render_local()
        self._render_global()

    def set_local_frame(self, current: int | None, total: int | None) -> None:
        """Source-frame number of the topmost visible layer at the
        playhead, plus the layer's last source frame. Numbers match
        what the user sees in the file names on disk.

        ``None`` for either side blanks out (no layer covering the
        playhead, e.g. inside a gap).
        """
        self._local_cur, self._local_max = current, total
        self._render_local()

    def set_global_frame(self, current: int | None, total: int | None) -> None:
        """Absolute master timeline frame and the timeline's last
        frame. Mirrors the values shown on the timeline ticks and the
        transport's frame readout."""
        self._global_cur, self._global_max = current, total
        self._render_global()

    # ---- Internal rendering -------------------------------------------

    def _render_local(self) -> None:
        self._local_lbl.setText(
            self._format_pair("Layer", self._local_cur, self._local_max),
        )

    def _render_global(self) -> None:
        self._global_lbl.setText(
            self._format_pair("Frame", self._global_cur, self._global_max),
        )

    def _format_pair(
        self, label: str, current: int | None, total: int | None,
    ) -> str:
        if current is None or total is None or total <= 0:
            return f"{label}  — / —"
        if self._mode == "tc":
            return (
                f"{label}  "
                f"{_frame_to_timecode(current, self._fps)} / "
                f"{_frame_to_timecode(total, self._fps)}"
            )
        width = len(str(total))
        return f"{label}  {current:0{width}d} / {total}"
