"""The :class:`ExportWorker` — :class:`QThread` running an :class:`ExportEngine`."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from img_player.export.engine import EngineResult, ExportEngine

log = logging.getLogger(__name__)


class ExportWorker(QThread):  # type: ignore[misc]
    """Runs an :class:`ExportEngine` off the GUI thread.

    Public signals:

    * ``progress(current, total, fps_running)`` — emitted after each
      frame. ``current`` is 1-based.
    * ``finished_ok(output_path: str, frames: int, duration_s: float)``
      — successful completion.
    * ``failed(message: str)`` — engine raised. The partial output is
      already cleaned up by the engine before the signal fires.
    * ``canceled(output_path: str, frames_written: int)`` — user-
      cancelled. The output is removed by the engine.
    """

    progress = Signal(int, int, float)
    finished_ok = Signal(str, int, float)
    failed = Signal(str)
    canceled = Signal(str, int)

    def __init__(self, engine: ExportEngine, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._engine = engine
        # Owner of the thread (typically the app) keeps a reference
        # so it survives until ``finished_ok``/``failed``/``canceled``
        # fires; once delivered, the receiver can `deleteLater()`
        # the worker.

    def cancel(self) -> None:
        """Forward the cancel request to the engine.

        Safe to call from the GUI thread — the engine's flag read
        is atomic in CPython, and the worker's loop checks it once
        per frame."""
        self._engine.cancel()

    def run(self) -> None:  # noqa: D401 — Qt's run signature
        try:
            result: EngineResult = self._engine.run(progress_cb=self._emit_progress)
        except Exception as err:  # pragma: no cover - log path
            log.exception("[export] engine raised")
            self.failed.emit(str(err))
            return
        out = str(result.output_path)
        if result.canceled:
            self.canceled.emit(out, result.frames_written)
        else:
            self.finished_ok.emit(out, result.frames_written, result.duration_s)

    # ------------------------------------------------------------------ Internals

    def _emit_progress(self, current: int, total: int, fps_running: float) -> None:
        # Throttle is OPTIONAL — Qt's queued connection handles GUI
        # back-pressure naturally (signals coalesce on a slow main
        # thread). We let every frame fire to keep the ETA accurate.
        self.progress.emit(int(current), int(total), float(fps_running))


def output_path_str(p: Path) -> str:
    """Helper for the failed/canceled signals — keep the public API
    string-typed so the receiver doesn't need a Path import to
    update a label."""
    return str(p)
