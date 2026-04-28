"""Modal dialog: pick one sequence among several found in a folder.

Shown when the user drops (or opens) a directory that contains more
than one image sequence. Without the dialog, the scanner silently
falls back to "the largest sequence in the folder", which is wrong
when an artist drops a mixed bag (renders + ref images, multiple AOV
passes split into siblings, etc.).

Layout — kept deliberately simple, one row per sequence:

    ┌── Pick a sequence ──────────────────────────────────────┐
    │                                                         │
    │   beauty.####.exr            1001-1240   (240 frames)   │
    │   diffuse.####.exr           1001-1240   (240 frames)   │
    │   plate.####.dpx             1001-1240   (240 frames)   │
    │   thumb.####.jpg             1-12        (12 frames)    │
    │                                                         │
    │                              [ Cancel ]  [ Open ]       │
    └─────────────────────────────────────────────────────────┘

Double-click on a row = accept (= the same as picking + Open). Up /
Down arrows + Enter also work. Cancel returns ``None`` from
:meth:`pick`, the caller skips the load.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from img_player.sequence.models import SequenceInfo
from img_player.ui.theme import F, H, S


class SequencePickerDialog(QDialog):  # type: ignore[misc]
    """Modal sequence chooser. Use :meth:`pick` for the convenience API."""

    def __init__(
        self,
        sequences: list[SequenceInfo],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick a sequence")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._sequences = sequences

        outer = QVBoxLayout(self)
        outer.setContentsMargins(S.LG, S.LG, S.LG, S.LG)
        outer.setSpacing(S.MD)

        header = QLabel(
            f"This folder contains {len(sequences)} sequences. "
            f"Pick one to load:"
        )
        header.setWordWrap(True)
        outer.addWidget(header)

        self._list = QListWidget()
        # Mono font on the list so frame ranges line up vertically —
        # easier to scan when several sequences share a base name and
        # only the range differs.
        self._list.setFont(F.mono(F.SIZE_SM))
        self._list.setMinimumHeight(160)
        # Stronger selection highlight than the global QSS default.
        # That default uses ACCENT_DIM (a muted orange) which gets
        # lost against the dark list background — users couldn't
        # tell which row was picked. Override with the bright accent
        # for the row + a clear text contrast, and keep a subtle
        # hover so keyboard / mouse users both get visual feedback.
        # Also a comfortable per-row padding so text doesn't hug the
        # edges in a font-mono list.
        self._list.setStyleSheet(
            f"""
            QListWidget {{
                background: #14161A;
                border: 1px solid #2A2D33;
                border-radius: 4px;
                outline: 0;
            }}
            QListWidget::item {{
                padding: 6px 10px;
                color: #D8D8D8;
            }}
            QListWidget::item:hover {{
                background: #1F2228;
            }}
            QListWidget::item:selected {{
                background: {H.ACCENT};
                color: #0A0A0A;
            }}
            QListWidget::item:selected:!active {{
                /* Same vivid colour even when the dialog loses focus
                   (clicking Open via mouse blurs the list briefly). */
                background: {H.ACCENT};
                color: #0A0A0A;
            }}
            """
        )
        for seq in sequences:
            item = QListWidgetItem(self._format_row(seq))
            item.setToolTip(str(seq.directory / seq.display_pattern()))
            self._list.addItem(item)
        # Pre-select the first row (= largest sequence — scan_all
        # returns largest first). Keyboard arrows work out of the box.
        if sequences:
            self._list.setCurrentRow(0)
        # Double-click is the universal "accept this row" gesture.
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        outer.addWidget(self._list, 1)

        # Standard button row — Cancel left, Open right (Qt auto-orders
        # for the platform). Open is the default so Enter accepts.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Open,
            parent=self,
        )
        open_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        if open_btn is not None:
            open_btn.setDefault(True)
            open_btn.setAutoDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------ Helpers

    @staticmethod
    def _format_row(seq: SequenceInfo) -> str:
        """One-line summary: pattern + range + count, padded for alignment."""
        pattern = seq.display_pattern()
        rng = f"{seq.first_frame}-{seq.last_frame}"
        count = f"({seq.frame_count} frames)"
        # Hand-tuned column widths — the dialog is wide enough.
        return f"{pattern:<32}  {rng:<14}  {count}"

    def selected_sequence(self) -> SequenceInfo | None:
        """Return the currently highlighted row's sequence, ``None`` if none."""
        row = self._list.currentRow()
        if 0 <= row < len(self._sequences):
            return self._sequences[row]
        return None

    # ------------------------------------------------------------------ Convenience

    @classmethod
    def pick(
        cls,
        sequences: list[SequenceInfo],
        parent: QWidget | None = None,
    ) -> SequenceInfo | None:
        """Show the dialog and return the user's pick (or ``None`` on cancel).

        Pure convenience wrapper around the QDialog dance — most call
        sites only need the chosen sequence, not the dialog instance.
        """
        dlg = cls(sequences, parent=parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.selected_sequence()
