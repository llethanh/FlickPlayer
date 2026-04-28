"""ABC for the export writers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from img_player.export.settings import ExportSettings


class ExportWriteError(RuntimeError):
    """Raised when the writer cannot open / write / close its output."""


class BaseWriter(ABC):
    """Common interface — open once, write_frame N times, close."""

    @abstractmethod
    def open(
        self, settings: ExportSettings, width: int, height: int, fps: float
    ) -> None:
        """Prepare the output. May create files / directories.

        ``width`` / ``height`` / ``fps`` are the *final* values after
        any source / preset / custom resolution has been resolved.
        Settings still carries the user-facing fields the writer
        needs (jpg quality, codec choice, etc.)."""

    @abstractmethod
    def write_frame(self, arr: np.ndarray, frame_idx: int) -> None:
        """Write one frame. ``arr`` is HxWxC uint8 or float in the
        renderer's output dtype contract — writers assert on the
        shape they expect.

        ``frame_idx`` is the export-relative index (0-based for
        video, ``settings.start_frame + i`` semantics already
        applied by the engine for image sequence file naming)."""

    @abstractmethod
    def close(self) -> None:
        """Finalize. Idempotent: calling twice is a no-op (the
        engine calls it from the success path AND from the abort
        path defensively)."""

    @abstractmethod
    def abort(self) -> None:
        """Discard the output entirely — close any partial files
        and remove what was written. Called on cancel or failure."""

    @abstractmethod
    def output_path(self) -> Path:
        """The "thing produced" — the directory for image sequences,
        the file for videos. Used by the engine in the success
        message."""
