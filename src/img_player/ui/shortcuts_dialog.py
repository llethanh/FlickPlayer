"""A modal dialog that lists every keyboard shortcut the app exposes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Playback",
        [
            ("Space / K", "Toggle play ↔ pause"),
            ("J", "Play in reverse"),
            ("L", "Play forward"),
            ("← / →", "Previous / next frame"),
            ("Shift + ← / →", "Skip ± 10 frames"),
            ("Home / End", "First / last frame of the sequence"),
        ],
    ),
    (
        "In / out range",
        [
            ("I", "Set in-point at the current frame"),
            ("O", "Set out-point at the current frame"),
            ("Shift + R", "Clear in / out range (back to full sequence)"),
        ],
    ),
    (
        "Color",
        [
            ("+ / -", "Nudge exposure by +/- 0.25 stops"),
        ],
    ),
    (
        "File",
        [
            ("Ctrl + O", "Open a file or sequence"),
            ("Ctrl + Q", "Quit img_player"),
        ],
    ),
    (
        "Drag & drop",
        [
            ("Drop a folder", "Scan it and load the largest sequence"),
            ("Drop a file", "Load the sequence that contains that frame"),
        ],
    ),
]


class ShortcutsDialog(QDialog):  # type: ignore[misc]
    """Static reference sheet of keyboard shortcuts and drop actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumSize(460, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        for section, rows in _SECTIONS:
            root.addWidget(self._build_section(section, rows))

        root.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        root.addWidget(buttons)

    def _build_section(self, title: str, rows: list[tuple[str, str]]) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        heading = QLabel(f"<b>{title}</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(heading)

        for keys, description in rows:
            row = QHBoxLayout()
            row.setContentsMargins(10, 0, 0, 0)
            row.setSpacing(12)
            key_label = QLabel(keys)
            key_label.setStyleSheet(
                "background: #333; color: #eee; padding: 1px 6px; border-radius: 3px;"
            )
            key_label.setMinimumWidth(140)
            row.addWidget(key_label)
            row.addWidget(QLabel(description), stretch=1)
            layout.addLayout(row)

        return wrapper
