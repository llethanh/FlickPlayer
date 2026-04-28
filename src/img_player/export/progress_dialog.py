"""The :class:`ExportProgressDialog` — non-modal progress UI."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Deque

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ExportProgressDialog(QDialog):  # type: ignore[misc]
    """Shows a progress bar + ETA + Cancel button.

    The hosting code wires the export worker's signals to the
    :meth:`update_progress`, :meth:`on_finished`, :meth:`on_failed`,
    :meth:`on_canceled` slots."""

    def __init__(
        self,
        *,
        total_frames: int,
        output_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporting…")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._total = max(1, int(total_frames))
        self._output_path = output_path
        self._cancel_requested = False
        self._fps_history: Deque[float] = deque(maxlen=20)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._main_label = QLabel(f"Rendering frame 0 / {self._total}")
        self._main_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self._main_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, self._total)
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        self._stats_label = QLabel("Speed: — · ETA: —")
        self._stats_label.setStyleSheet("color: #B5B5B8; font-size: 11px;")
        layout.addWidget(self._stats_label)

        self._path_label = QLabel(f"Output: {output_path}")
        self._path_label.setStyleSheet("color: #8A8A8E; font-size: 11px;")
        self._path_label.setWordWrap(True)
        layout.addWidget(self._path_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._request_cancel)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------ Slots

    def update_progress(self, current: int, total: int, fps_running: float) -> None:
        self._total = max(1, int(total))
        self._progress.setMaximum(self._total)
        self._progress.setValue(int(current))
        self._main_label.setText(f"Rendering frame {current} / {total}")
        self._fps_history.append(float(fps_running))
        avg_fps = sum(self._fps_history) / max(1, len(self._fps_history))
        remaining = max(0, total - current)
        eta_s = remaining / avg_fps if avg_fps > 1e-3 else 0.0
        self._stats_label.setText(
            f"Speed: {avg_fps:.1f} fps · ETA: {self._format_eta(eta_s)}"
        )

    def on_finished(self, output_path: str, frames: int, duration_s: float) -> None:
        self._main_label.setText(
            f"Done — {frames} frames in {duration_s:.1f} s"
        )
        self._path_label.setText(f"Saved to: {output_path}")
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.accept)

    def on_failed(self, message: str) -> None:
        self._main_label.setText(f"Export failed: {message}")
        self._main_label.setStyleSheet("color: #E84A4A; font-weight: 600;")
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    def on_canceled(self, output_path: str, frames: int) -> None:
        del output_path
        self._main_label.setText(f"Canceled after {frames} frames")
        self._cancel_btn.setText("Close")
        self._cancel_btn.clicked.disconnect()
        self._cancel_btn.clicked.connect(self.reject)

    # ------------------------------------------------------------------ Cancel

    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def _request_cancel(self) -> None:
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Canceling…")
        # The hosting code also connects to ``cancel_btn.clicked`` to
        # forward the cancel to the worker — that connection is set
        # up on construction by the orchestrator.

    @property
    def cancel_button(self) -> QPushButton:
        """Public accessor — the orchestrator wires its
        ``worker.cancel`` to this button's ``clicked`` signal."""
        return self._cancel_btn

    # ------------------------------------------------------------------ Helpers

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if seconds <= 0:
            return "—"
        if seconds < 60:
            return f"{seconds:.0f} s"
        m, s = divmod(int(seconds), 60)
        if m < 60:
            return f"{m} m {s:02d} s"
        h, m = divmod(m, 60)
        return f"{h} h {m:02d} m"
