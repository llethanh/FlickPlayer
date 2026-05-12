"""Auto-reload trigger when source files change on disk.

Wraps :class:`QFileSystemWatcher` to watch the parent directories of
every loaded layer's sequence. When the OS reports a change, a small
debounce timer waits ~200 ms (re-renders typically write 100+ files
back-to-back, no point firing the smart-reload pipeline for each one)
and then emits :pyattr:`sources_changed`. The app wires that signal
to the existing ``_on_reload_sequence`` smart-rescan path — which
already does the mtime-diff, drops stale RAM, and naturally bypasses
the disk-cache entries for the new key (mtime is part of the key).

Why directory-level rather than file-level?
-------------------------------------------

A 200-frame sequence under per-file watching would consume 200 OS
watch handles; under Windows that hits a per-process limit quickly
(8192) once you stack multiple layers. Watching the parent directory
catches add / delete / mtime-change of every contained file with a
single handle. The downside — we get notified for unrelated files in
the same directory — is harmless because the smart-rescan is keyed on
the sequence's own filename pattern; foreign files don't show up in
the diff.

Lifetime
--------

The watcher is created once at app start with the main window as its
parent (Qt object tree owns it). The app calls
:meth:`set_watched_layers` after every ``layers_changed`` signal so
the watch list stays in sync with the loaded layer stack.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from img_player.layers.models import Layer


# Debounce interval: a typical re-render hits the disk in a burst of
# tens to hundreds of file events back-to-back. We wait 200 ms after
# the last event before firing — long enough to coalesce a normal
# write burst, short enough that the user perceives the auto-reload
# as "happening as I save".
_DEBOUNCE_MS = 200


class SourceWatcher(QObject):
    """Emits :pyattr:`sources_changed` after a debounced FS change."""

    sources_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._fs = QFileSystemWatcher(self)
        self._fs.directoryChanged.connect(self._on_dir_changed)
        # The fileChanged signal would fire too — but we only register
        # directories so it stays silent. Wired defensively in case a
        # later refactor adds per-file watching.
        self._fs.fileChanged.connect(self._on_dir_changed)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_changed)

        self._watched: set[str] = set()

    # ------------------------------------------------------------------ API

    def set_watched_layers(self, layers: Iterable[Layer]) -> None:
        """Sync the watch list to the unique source directories of ``layers``.

        Computed as the set of every layer's ``sequence.directory``.
        Diffed against the previous set so we only call ``addPaths`` /
        ``removePaths`` for the delta — cheaper than tearing down and
        rebuilding every time the user toggles a layer's visibility.

        Non-existent directories are skipped silently — happens if the
        user passes a deleted folder via CLI, or during teardown when
        the layer-stack signals fire mid-reset.
        """
        desired: set[str] = set()
        for layer in layers:
            seq = getattr(layer, "sequence", None)
            if seq is None:
                continue
            directory = getattr(seq, "directory", None)
            if directory is None:
                continue
            path = Path(directory)
            if not path.is_dir():
                continue
            desired.add(str(path))

        to_add = desired - self._watched
        to_remove = self._watched - desired
        if to_remove:
            self._fs.removePaths(sorted(to_remove))
        if to_add:
            self._fs.addPaths(sorted(to_add))
        self._watched = desired

    def watched_dirs(self) -> tuple[str, ...]:
        """Snapshot of currently watched directory paths. For diagnostics / tests."""
        return tuple(sorted(self._watched))

    def stop(self) -> None:
        """Stop watching and cancel any pending debounce. Idempotent."""
        if self._debounce.isActive():
            self._debounce.stop()
        if self._watched:
            self._fs.removePaths(sorted(self._watched))
            self._watched.clear()

    # ------------------------------------------------------------------ Internals

    def _on_dir_changed(self, _path: str) -> None:
        # Restart the debounce on every event — Qt's QTimer is fine
        # being restarted while already active (it just resets the
        # countdown). Net effect: we fire 200 ms after the LAST event
        # in a burst, not 200 ms after the first.
        self._debounce.start()

    def _emit_changed(self) -> None:
        self.sources_changed.emit()
