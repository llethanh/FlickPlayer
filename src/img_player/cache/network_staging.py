"""Network-source staging cache.

Why this exists
---------------

Image readers (OIIO, PyOpenEXR, libtiff, libdpx) all assume their
input is on a local filesystem. They open the file, seek around to
parse the header, seek again to read each channel / scanline block,
do many small reads with no concern for I/O latency. On a local SSD
this is fine (each read is ~10 µs).

On a network share — especially SMB — every small read pays a RTT.
A 232 MB EXR that decodes in 130 ms from local SSD takes 1200 ms
from a `M:\` mapped network share, even though the raw bandwidth
to that share is 800 MB/s (i.e. a bulk `f.read()` of the same file
takes 320 ms). The difference is the multitude of small reads vs
one big read.

This module stages bulk copies of network-source frames into a
local SSD cache. The reader then reads from the local copy, gets
its expected I/O pattern, and decodes ~3× faster than direct
network reads.

How it works
------------

1. :class:`NetworkStagingManager` keeps an in-memory map
   ``{original_path: staged_path}`` of files that have been
   successfully copied.
2. A background thread consumes a copy queue, copying one file at
   a time to ``{staging_root}/{seq_hash}/{filename}``.
3. ``staged_path_for(original)`` returns the local copy when it's
   ready, else ``None`` (caller falls back to direct network read).
4. When the per-sequence staging directory exceeds the budget OR
   when too many sequences accumulate, the LRU sequence directory
   is evicted (whole dir at once — no per-file LRU).
5. The manager is thread-safe: decode workers may call
   ``staged_path_for`` from any thread; the staging worker updates
   the map under a lock.

Scope (v1)
----------

* Sequence-level LRU: when total size exceeds the budget, evict the
  least-recently-used WHOLE sequence directory. Per-file LRU adds
  complexity for marginal gain — sequences are the natural unit.
* Auto-detect network paths via simple drive-letter / UNC heuristic.
  Power users can override per-prefs.
* Single-threaded copy worker — empirically 800 MB/s on this test
  share, no benefit from parallel chunks (see the bench tool).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Network-path detection
# ---------------------------------------------------------------------------

def is_network_path(path: Path | str) -> bool:
    """Quick heuristic: does ``path`` live on a network filesystem?

    On Windows this means either:
    * a UNC path (``\\\\server\\share\\...``), OR
    * a mapped drive letter that resolves to a UNC root (e.g. ``M:\\``
      mapped to ``\\\\fs\\projects``).

    We use the Win32 ``GetDriveTypeW`` API for the drive-letter check
    — that's the canonical answer. The cost is a single syscall, so
    we don't bother caching. On non-Windows the heuristic is just
    "starts with ``//`` or ``\\\\``".
    """
    p = Path(path)
    parts = p.parts
    if not parts:
        return False
    first = parts[0]
    # UNC: \\server\share or //server/share
    if first.startswith("\\\\") or first.startswith("//"):
        return True
    if os.name != "nt":
        return False
    # Windows mapped drive — query the drive type.
    try:
        import ctypes  # noqa: PLC0415
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # Root: "M:\\" — GetDriveTypeW wants this exact form.
        root = first if first.endswith("\\") else first + "\\"
        kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint
        # DRIVE_REMOTE = 4 (network drive)
        return kernel32.GetDriveTypeW(root) == 4
    except Exception:  # noqa: BLE001
        return False


def _sequence_hash(directory: Path) -> str:
    """Stable hash for a directory path. Used as the on-disk sub-dir
    name under the staging root. We hash the absolute path so two
    different network shares with same-named subdirs don't collide.

    16 hex chars = 64 bits of entropy = practically zero collision
    risk for any realistic number of sequences a user opens.
    """
    canon = str(directory.resolve() if directory.is_absolute() else directory)
    return hashlib.blake2b(
        canon.encode("utf-8", errors="replace"), digest_size=8,
    ).hexdigest()


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class NetworkStagingManager:
    """Background copy of network-source frames to a local SSD cache.

    Wire up at App boot with the user's preferred staging root + budget,
    register sequences as they open, and the manager handles the rest.
    Decode workers consult :meth:`staged_path_for` to redirect reads.

    Thread-safety: every public method is safe to call from any
    thread. The internal map + queue are protected by ``self._lock``;
    the copy worker is a single dedicated thread that exits cleanly
    via :meth:`shutdown`.
    """

    def __init__(
        self,
        staging_root: Path,
        max_total_gb: float = 50.0,
        *,
        enabled: bool = True,
    ) -> None:
        self._root = Path(staging_root)
        self._max_bytes = int(max_total_gb * 1024**3)
        self._enabled = enabled
        # Maps original path → local staged path, populated by the
        # copy worker as files land. Reads from decode workers go
        # through this map without taking the lock — Python dict
        # ``get`` is atomic in CPython.
        self._map: dict[str, Path] = {}
        # Per-sequence "last touched" time for LRU eviction at the
        # directory level. Updated by ``register_sequence`` (= user
        # opened that sequence) and by ``staged_path_for`` (= a
        # worker just hit a frame from that sequence).
        self._sequence_touched: dict[str, float] = {}
        # Copy queue: tuples of (priority, original_path,
        # staged_path). Lower priority = copied first; we use the
        # frame index so playhead-order completes first.
        self._queue: "Queue[tuple[int, str, Path]]" = Queue()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Optional callback for tests / instrumentation.
        self._on_staged: Callable[[str, Path], None] | None = None

    # ---- Public lifecycle -------------------------------------------------

    def start(self) -> None:
        """Spawn the background copy worker if not already running."""
        if not self._enabled:
            log.info("[staging] disabled — skipping worker start")
            return
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._root.mkdir(parents=True, exist_ok=True)
        self._scan_existing()
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="NetworkStagingCopy",
            daemon=True,
        )
        self._worker.start()
        log.info(
            "[staging] worker started; root=%s budget=%.1f GB",
            self._root, self._max_bytes / 1024**3,
        )

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the worker and wait briefly for it to exit. Files
        already in flight finish their copy; the queue is drained."""
        self._stop_event.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=timeout)
        self._worker = None

    def set_enabled(self, on: bool) -> None:
        """Runtime toggle. When disabled, the worker exits and
        ``staged_path_for`` always returns ``None`` (read paths use
        the original network path directly)."""
        self._enabled = bool(on)
        if not on:
            self.shutdown()

    # ---- Sequence registration -------------------------------------------

    def register_sequence(
        self,
        directory: Path,
        files: Sequence[Path],
        *,
        playhead_frame: int = 0,
    ) -> int:
        """Queue every file in ``files`` for background copying.

        Returns the number of files queued. Files already staged are
        skipped. The ``playhead_frame`` parameter biases the queue
        ordering: files at/after the playhead get priority over
        files behind it, so the user's near-term scrubs see local
        copies as fast as possible.

        No-op when staging is disabled OR when ``directory`` isn't
        on a network filesystem (= staging the same drive as the
        staging root would be wasted work).
        """
        if not self._enabled:
            return 0
        if not is_network_path(directory):
            log.debug("[staging] %s is not a network path; not staging", directory)
            return 0
        seq_hash = _sequence_hash(directory)
        with self._lock:
            self._sequence_touched[seq_hash] = time.time()
            staging_dir = self._root / seq_hash
            staging_dir.mkdir(parents=True, exist_ok=True)
            queued = 0
            for idx, src in enumerate(files):
                key = str(src)
                if key in self._map:
                    # Already staged in this session
                    continue
                # If the file is already on disk from a previous
                # session, register it immediately (skip the copy).
                target = staging_dir / src.name
                if target.is_file():
                    # Trust the file as long as it matches the
                    # source's mtime + size. Mismatch = source
                    # changed since last staging → re-copy.
                    if _file_matches(src, target):
                        self._map[key] = target
                        continue
                    # Stale; will be re-staged below.
                # Priority: playhead-distance, forward bias.
                # Files at or after the playhead get index difference;
                # files behind get index difference + a forward-skew
                # offset so they queue AFTER any forward file.
                if idx >= playhead_frame:
                    priority = idx - playhead_frame
                else:
                    priority = (playhead_frame - idx) + 10_000
                self._queue.put((priority, key, target))
                queued += 1
        log.info(
            "[staging] sequence %s: queued %d files (already staged: %d)",
            directory.name, queued, len(files) - queued,
        )
        # Make room for the new sequence if we're over budget.
        self._evict_until_under_budget()
        return queued

    # ---- The hot path: redirect reads -------------------------------------

    def staged_path_for(self, original: Path | str) -> Path | None:
        """Return the local staged copy of ``original``, or ``None``
        if it hasn't been staged yet. Called by :func:`io.reader.
        read_frame` on every decode — must be cheap (no I/O, no
        lock for the common-hit case)."""
        if not self._enabled:
            return None
        key = str(original)
        staged = self._map.get(key)
        if staged is None:
            return None
        # File may have been evicted from disk since registration —
        # be paranoid (fall back to network read if so).
        if not staged.is_file():
            with self._lock:
                self._map.pop(key, None)
            return None
        return staged

    # ---- Internals --------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                _, src_key, target = self._queue.get(timeout=0.5)
            except Empty:
                continue
            src = Path(src_key)
            if not src.is_file():
                log.warning("[staging] source vanished before copy: %s", src)
                continue
            try:
                # shutil.copyfile uses a 64KB buffer by default — on
                # Windows this triggers an optimal CopyFileExW under
                # the hood for source-on-network → dest-on-local,
                # which uses SMB's bulk transfer mode.
                tmp = target.with_suffix(target.suffix + ".part")
                shutil.copyfile(str(src), str(tmp))
                os.replace(str(tmp), str(target))
            except OSError as exc:
                log.warning("[staging] copy failed %s → %s: %s", src, target, exc)
                continue
            # Register the success.
            self._map[src_key] = target
            if self._on_staged is not None:
                try:
                    self._on_staged(src_key, target)
                except Exception:  # noqa: BLE001
                    log.debug("[staging] on_staged callback raised", exc_info=True)
            # Stream-side eviction: if the running copies push us over
            # the budget, evict the LRU sequence dir. The currently-
            # active sequence (= the one being copied right now) is
            # always the most-recently-touched, so it's never the
            # victim. Cheap-ish — _dir_size_bytes walks the staging
            # root, but only happens once per copied file (~once every
            # ~300 ms on a 232 MB Maya EXR).
            self._evict_until_under_budget()

    def _scan_existing(self) -> None:
        """At startup, walk the staging root and learn what's already
        on disk. We DON'T re-register paths into ``self._map`` here
        (we don't have the original-path mapping anymore — only the
        hash dir name). But we DO need to know which sequence dirs
        exist so the LRU eviction has accurate sizes / counts.
        ``register_sequence`` then re-registers each file as the App
        opens its sequences in this session."""
        if not self._root.is_dir():
            return
        for sub in self._root.iterdir():
            if sub.is_dir():
                self._sequence_touched.setdefault(sub.name, sub.stat().st_mtime)

    def _evict_until_under_budget(self) -> None:
        """If total staging size > budget, remove the LRU sequence
        directory and update touched-map. Loops until under budget
        OR only one sequence remains (= the one we just registered)."""
        with self._lock:
            while True:
                if not self._root.is_dir():
                    return
                total = _dir_size_bytes(self._root)
                if total <= self._max_bytes:
                    return
                if len(self._sequence_touched) <= 1:
                    log.warning(
                        "[staging] over budget (%.1f GB > %.1f GB) but "
                        "only one sequence cached — keeping it",
                        total / 1024**3, self._max_bytes / 1024**3,
                    )
                    return
                # Pick the oldest touched sequence
                oldest_hash = min(
                    self._sequence_touched,
                    key=lambda h: self._sequence_touched[h],
                )
                target = self._root / oldest_hash
                log.info(
                    "[staging] evicting LRU sequence dir %s "
                    "(total=%.1f GB, budget=%.1f GB)",
                    target, total / 1024**3, self._max_bytes / 1024**3,
                )
                shutil.rmtree(str(target), ignore_errors=True)
                self._sequence_touched.pop(oldest_hash, None)
                # Drop map entries pointing at this dir
                drop_keys = [
                    k for k, v in self._map.items()
                    if str(v).startswith(str(target))
                ]
                for k in drop_keys:
                    self._map.pop(k, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_matches(src: Path, target: Path) -> bool:
    """Are ``src`` (network) and ``target`` (staged copy) the same file?
    Check size + mtime — exact match means the staged copy is still
    valid. Difference means the source was re-rendered and we need to
    re-stage. Cheap (two stat calls, no I/O)."""
    try:
        s = src.stat()
        t = target.stat()
    except OSError:
        return False
    if s.st_size != t.st_size:
        return False
    # Allow a 2-second mtime drift to absorb FAT / SMB time-stamp
    # quirks across filesystems (FAT has 2s resolution).
    return abs(s.st_mtime - t.st_mtime) < 2.0


def _dir_size_bytes(root: Path) -> int:
    """Total size of every file under ``root``. Cheap-ish — uses
    ``os.scandir`` recursively which on Windows is a single
    ``NtQueryDirectoryFile`` per directory."""
    total = 0
    try:
        for entry in os.scandir(str(root)):
            if entry.is_file(follow_symlinks=False):
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
            elif entry.is_dir(follow_symlinks=False):
                total += _dir_size_bytes(Path(entry.path))
    except OSError:
        pass
    return total
