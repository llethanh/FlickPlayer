"""The :class:`ExportEngine` — orchestrates render → write across a frame range.

This is the loop body. The :class:`ExportWorker` runs it on a Qt
thread and pipes the engine's progress callbacks through Qt
signals; tests can also drive the engine synchronously.

The engine is deliberately Qt-free at its API surface — the only
Qt dependency comes from the renderer (QImage / QPainter for the
annotation bake). That keeps the loop testable without spinning
up a QThread.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from img_player.color.ocio_manager import OCIOManager
from img_player.export.renderer import (
    CompareRenderContext,
    FrameRenderer,
    RenderContext,
)
from img_player.export.settings import ExportSettings
from img_player.export.writers import BaseWriter, build_writer
from img_player.sequence.channels import ChannelSelection
from img_player.sequence.models import SequenceInfo

log = logging.getLogger(__name__)


# Codecs reject odd dimensions. We round UP (ceiling-to-even) on
# output_size when the user picks a custom resolution that lands odd.
def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


# Upper bound on the image-sequence export worker pool. Both heavy
# stages (OCIO applyRGB, OIIO encode) release the GIL, so a thread
# pool scales ~5-7× on 8 workers (measured); beyond that, disk-write
# contention and the per-frame RAM footprint (one decoded float RGBA
# frame held in flight per worker) outweigh the gains.
_EXPORT_MAX_WORKERS = 8


@dataclass
class EngineResult:
    """Returned by :meth:`ExportEngine.run`. Carries enough state for
    the success / cancel / fail messages in the UI."""

    output_path: Path
    frames_written: int
    duration_s: float
    canceled: bool = False


class ExportEngine:
    """Owns one export run: open writer, loop frames, close writer."""

    def __init__(
        self,
        settings: ExportSettings,
        sequence: SequenceInfo,
        annotation_store,
        ocio_manager: OCIOManager | None,
        *,
        source_colorspace: str | None,
        display: str | None,
        view: str | None,
        sidecar_source: Path | None = None,
        channel_selection: ChannelSelection | None = None,
        compare: CompareRenderContext | None = None,
    ) -> None:
        self._settings = settings
        self._sequence = sequence
        # Capture the live channel state so the export reproduces the
        # exact channel the user has on screen. ``None`` falls back to
        # the legacy default-channels path.
        self._channel_selection = channel_selection
        # The CPU OCIO processor is built once at engine setup. If
        # OCIO isn't available or the user disabled the transform we
        # leave it at None and the renderer skips the colour step.
        ocio_proc = None
        if ocio_manager is not None and settings.apply_display_transform:
            ocio_proc = self._build_cpu_processor(
                ocio_manager,
                source_colorspace=source_colorspace,
                display=display,
                view=view,
            )
        # Compare overlay only honoured when the user ticked the
        # bake-compare option AND the caller passed a live snapshot.
        # Either condition flipping off → None → renderer falls back
        # to the single-sequence path.
        compare_ctx = compare if settings.bake_compare else None
        ctx = RenderContext(
            sequence=sequence,
            annotation_store=annotation_store if settings.bake_annotations else None,
            ocio_cpu_processor=ocio_proc,
            channel_selection=channel_selection,
            compare=compare_ctx,
        )
        self._renderer = FrameRenderer(ctx, settings)
        self._sidecar_source = sidecar_source
        # Cancel flag set by the worker thread. Plain attribute reads
        # are atomic in CPython.
        self._cancel = False
        # The writer is built lazily inside ``run()`` so a failed
        # OCIO setup doesn't leave a half-opened video container on
        # disk — we want the construction to fail BEFORE we touch any
        # output file.
        self._writer: BaseWriter | None = None

    # ------------------------------------------------------------------ Public

    @property
    def total_frames(self) -> int:
        return self._settings.total_frames

    def cancel(self) -> None:
        """Mark the engine for cancellation. The next loop iteration
        notices and returns. Idempotent."""
        self._cancel = True

    def discard_partial_output(self) -> None:
        """Delete whatever the writer wrote before cancellation.

        Called by the export orchestrator when the user opts to
        discard partial files in the cancel-confirmation dialog.
        Safe to call after :meth:`run` returned with
        ``canceled=True`` — the writer was just closed cleanly
        there, so its file list / output path is still valid.
        Video writers always discard regardless (a mid-encode
        container is unreadable anyway); the prompt happens for
        image-sequence writers only.
        """
        if self._writer is None:
            return
        try:
            self._writer.abort()
        except Exception:  # pragma: no cover — defensive
            log.exception("[export] discard_partial_output failed")

    def run(
        self,
        progress_cb: Callable[[int, int, float], None] | None = None,
    ) -> EngineResult:
        """Synchronously execute the export.

        ``progress_cb(current_frame, total, fps_running)`` is invoked
        AFTER each successful frame write. ``current_frame`` is
        1-based for the user's mental model ("frame 247 / 500").

        Raises :exc:`Exception` on any unrecoverable I/O error — the
        worker catches and routes to a ``failed`` Qt signal.
        """
        settings = self._settings
        settings.validate()

        out_w, out_h = self._resolve_output_size()
        out_fps = self._resolve_output_fps()

        # User-overridden basename wins; otherwise fall back to the
        # source sequence's base_name (legacy behaviour). Strips
        # trailing separators / spaces so a stem like ``render._``
        # produces ``render.0001.png`` rather than ``render.._0001.png``.
        custom = (settings.basename or "").strip()
        if custom:
            basename = custom.rstrip("._- ") or "export"
        else:
            basename = self._sequence.base_name.rstrip("._-") or "export"
        self._writer = build_writer(settings, basename=basename)
        self._writer.open(settings, out_w, out_h, out_fps)

        start = time.monotonic()
        try:
            # Image-sequence output writes one independent file per
            # frame, so the OCIO + resize + bake + encode tail can fan
            # out across a thread pool (those stages release the GIL).
            # Video output must stay serial — the container encoder
            # demands frames in order. Tiny exports also stay serial:
            # below the pipeline depth the pool can't fill, the thread
            # overhead isn't worth it, and the serial path gives
            # frame-exact cancellation (the parallel path can flush a
            # few already-decoded frames past the cancel point).
            parallel_floor = self._worker_count() * 2
            if settings.is_image_sequence and settings.total_frames > parallel_floor:
                frames_written, canceled = self._run_parallel(
                    settings, out_w, out_h, start, progress_cb,
                )
            else:
                frames_written, canceled = self._run_serial(
                    settings, out_w, out_h, start, progress_cb,
                )
            # Close the writer cleanly so partial files stay readable on
            # disk. On cancel the orchestrator decides whether to keep
            # or discard them via :meth:`discard_partial_output` after
            # asking the user — auto-deleting here would steal that
            # choice.
            self._writer.close()
            if canceled:
                return EngineResult(
                    output_path=self._writer.output_path(),
                    frames_written=frames_written,
                    duration_s=time.monotonic() - start,
                    canceled=True,
                )
            # Optional sidecar copy.
            if (
                settings.bake_annotations
                and settings.copy_sidecar
                and self._sidecar_source is not None
                and self._sidecar_source.exists()
            ):
                try:
                    target = settings.output_dir / self._sidecar_source.name
                    shutil.copyfile(self._sidecar_source, target)
                except OSError:
                    log.exception(
                        "[export] failed to copy sidecar %s", self._sidecar_source,
                    )
            return EngineResult(
                output_path=self._writer.output_path(),
                frames_written=frames_written,
                duration_s=time.monotonic() - start,
                canceled=False,
            )
        except Exception:
            # Best-effort cleanup of the partial output.
            try:
                if self._writer is not None:
                    self._writer.abort()
            except Exception:  # pragma: no cover — defensive
                log.exception("[export] secondary error during abort")
            raise
        finally:
            # Release any video decoders the renderer opened (no-op for
            # image-sequence exports).
            try:
                self._renderer.close()
            except Exception:  # pragma: no cover — defensive
                log.debug("[export] renderer close failed", exc_info=True)

    # ------------------------------------------------------------------ Loop bodies

    def _run_serial(
        self,
        settings: ExportSettings,
        out_w: int,
        out_h: int,
        start: float,
        progress_cb: Callable[[int, int, float], None] | None,
    ) -> tuple[int, bool]:
        """Original in-order loop. Used for video output (the encoder
        needs frames sequentially) and as the simple reference path.

        Returns ``(frames_written, canceled)``.
        """
        frames_written = 0
        for i in range(settings.total_frames):
            if self._cancel:
                log.info("[export] canceled at frame %d / %d",
                         i, settings.total_frames)
                return frames_written, True
            source_frame = settings.in_frame + i
            arr = self._renderer.render(source_frame, (out_w, out_h))
            self._writer.write_frame(arr, i)
            frames_written += 1
            if progress_cb is not None:
                elapsed = max(1e-6, time.monotonic() - start)
                progress_cb(
                    frames_written, settings.total_frames,
                    frames_written / elapsed,
                )
        return frames_written, False

    def _run_parallel(
        self,
        settings: ExportSettings,
        out_w: int,
        out_h: int,
        start: float,
        progress_cb: Callable[[int, int, float], None] | None,
    ) -> tuple[int, bool]:
        """Pipelined loop for image-sequence output.

        The decode stays on this (producer) thread — mandatory for the
        single non-thread-safe PyAV :class:`VideoSource`, and harmless
        for OIIO image reads. Each decoded frame's heavy tail
        (:meth:`FrameRenderer.finalize` → OCIO + resize + bake) and the
        PNG/EXR encode are submitted to a worker pool. The writer names
        each file from its frame index, so out-of-order completion is
        fine.

        In-flight work is capped at ``2 × workers`` so the producer
        can't race ahead and balloon RAM with decoded frames.

        Returns ``(frames_written, canceled)``.
        """
        workers = self._worker_count()
        max_inflight = max(2, workers * 2)
        frames_written = 0
        canceled = False
        inflight: deque = deque()

        def drain_one() -> None:
            nonlocal frames_written
            fut = inflight.popleft()
            fut.result()  # re-raise any worker exception on this thread
            frames_written += 1
            if progress_cb is not None:
                elapsed = max(1e-6, time.monotonic() - start)
                progress_cb(
                    frames_written, settings.total_frames,
                    frames_written / elapsed,
                )

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="export",
        ) as ex:
            for i in range(settings.total_frames):
                if self._cancel:
                    log.info("[export] canceled at frame %d / %d",
                             i, settings.total_frames)
                    canceled = True
                    break
                source_frame = settings.in_frame + i
                arr, finalized = self._renderer.decode_raw(
                    source_frame, out_w, out_h,
                )
                inflight.append(ex.submit(
                    self._finalize_and_write,
                    arr, finalized, source_frame, i, out_w, out_h,
                ))
                while len(inflight) >= max_inflight:
                    drain_one()
            # Drain whatever is still in flight — including after a
            # cancel, so already-submitted frames finish cleanly rather
            # than leaving half-written files.
            while inflight:
                drain_one()
        # A cancel can land mid-drain (the progress callback that flips
        # the flag fires from ``drain_one``), after the producer loop
        # has exited. Report cancellation whenever the flag is set.
        return frames_written, canceled or self._cancel

    def _finalize_and_write(
        self,
        arr,
        finalized: bool,
        source_frame: int,
        frame_idx: int,
        out_w: int,
        out_h: int,
    ) -> None:
        """Worker task: finish the render tail and encode one frame."""
        final = (
            arr if finalized
            else self._renderer.finalize(arr, source_frame, out_w, out_h)
        )
        self._writer.write_frame(final, frame_idx)

    @staticmethod
    def _worker_count() -> int:
        return max(1, min(_EXPORT_MAX_WORKERS, os.cpu_count() or 4))

    # ------------------------------------------------------------------ Internals

    def _resolve_output_size(self) -> tuple[int, int]:
        """Source-or-explicit + even-dimensions guard for video."""
        if self._settings.width is not None and self._settings.height is not None:
            w = self._settings.width
            h = self._settings.height
        else:
            w = self._sequence.width or 1920
            h = self._sequence.height or 1080
        if self._settings.is_video:
            w = _even(w)
            h = _even(h)
        return w, h

    def _resolve_output_fps(self) -> float:
        if self._settings.fps is not None and self._settings.fps > 0:
            return float(self._settings.fps)
        return float(self._sequence.fps_default or 24.0)

    @staticmethod
    def _build_cpu_processor(
        manager: OCIOManager,
        *,
        source_colorspace: str | None,
        display: str | None,
        view: str | None,
    ):
        """Build a CPU display-view processor from the OCIO manager.

        Falls back to ``getDefaultCpuProcessor`` on the resolved
        :class:`PyOpenColorIO.Processor`. Returns ``None`` if any
        dependency is missing — the renderer treats that as
        "skip the colour step".
        """
        try:
            src = source_colorspace or manager.role("scene_linear") or "Linear Rec.709 (sRGB)"
            disp = display or manager.default_display()
            v = view or manager.default_view(disp)
            proc = manager.get_display_view_processor(src, disp, v)
            return proc.getDefaultCPUProcessor()
        except Exception:
            log.exception("[export] failed to build OCIO CPU processor; baking raw")
            return None
