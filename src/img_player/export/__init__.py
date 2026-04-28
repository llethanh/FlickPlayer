"""Export subsystem (v0.5.0).

Public API exposed to the rest of the app:

* :class:`ExportSettings` — every dialog choice as an immutable
  dataclass. The engine consumes a ``ExportSettings`` instance and
  mutates nothing else; persisted "last-used" defaults are produced
  by :meth:`ExportSettings.to_prefs_dict` and rebuilt by
  :meth:`ExportSettings.from_prefs_dict`.
* :class:`ExportEngine` — synchronous orchestrator. Reads the source
  via :class:`FrameRenderer`, pushes the rendered frames into the
  selected writer. Exposes a ``cancel()`` flag for the worker thread.
* :class:`ExportWorker` — :class:`QThread` wrapper. Owns the engine,
  emits progress / finished / failed / canceled Qt signals.
* :class:`ExportDialog` — the user-facing :class:`QDialog`. Produces
  an :class:`ExportSettings` on accept.
* :class:`ExportProgressDialog` — non-modal progress UI driven by
  the worker's ``progress`` signal.

The internal modules are deliberately small and side-effect-free
where possible (settings + renderer + writers are pure / functional;
engine + worker carry the I/O + threading concerns).
"""

from __future__ import annotations

from img_player.export.settings import (
    AVAILABLE_IMAGE_FORMATS,
    AVAILABLE_VIDEO_FORMATS,
    EXR_COMPRESSIONS,
    PRORES_PROFILES,
    RESOLUTION_PRESETS,
    ExportFormat,
    ExportFormatKind,
    ExportSettings,
    ExportSettingsError,
    estimate_size_bytes,
    format_bytes,
)

__all__ = (
    "AVAILABLE_IMAGE_FORMATS",
    "AVAILABLE_VIDEO_FORMATS",
    "EXR_COMPRESSIONS",
    "PRORES_PROFILES",
    "RESOLUTION_PRESETS",
    "ExportFormat",
    "ExportFormatKind",
    "ExportSettings",
    "ExportSettingsError",
    "estimate_size_bytes",
    "format_bytes",
)
