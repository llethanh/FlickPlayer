"""Small "flushing disk cache" label shown during app exit.

When the user closes the app with a backlog of pending disk-cache
writes, :meth:`DiskCache.shutdown` blocks up to 10 s while the writer
thread drains its queue. Without feedback the user sees a frozen
window — this widget makes the pause legible by counting down the
pending frames live.

Used only at exit. Frameless, always-on-top, no buttons — it's a
status bubble, not a dialog. Auto-closed by the app shutdown path
once the drain returns.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class FlushIndicator(QWidget):
    """Tiny non-interactive popup that ticks down a counter."""

    def __init__(self, initial_remaining: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._initial = max(1, initial_remaining)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Dark themed bubble matching the rest of the app. Inline
        # stylesheet keeps the widget self-contained (it runs during
        # shutdown when the main theme might already be tearing down).
        self.setStyleSheet(
            """
            FlushIndicator {
                background-color: #1f1f1f;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
            }
            QLabel {
                color: #e6e6e6;
                font-size: 12px;
                padding: 4px 8px;
            }
            QLabel#title {
                font-weight: 600;
                color: #f0a020;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self._title = QLabel("Flushing disk cache…", self)
        self._title.setObjectName("title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title)

        self._counter = QLabel(f"{initial_remaining} frame(s) pending", self)
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._counter)

        self.setFixedSize(240, 70)
        self._recenter_on_parent()

    def _recenter_on_parent(self) -> None:
        """Place the bubble at the center of the parent (or screen)."""
        parent = self.parentWidget()
        if parent is None:
            return
        geo = parent.geometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def update_remaining(self, remaining: int) -> None:
        """Refresh the counter line. Called from the drain progress callback."""
        if remaining <= 0:
            self._counter.setText("done — closing…")
        else:
            self._counter.setText(f"{remaining} frame(s) pending")
