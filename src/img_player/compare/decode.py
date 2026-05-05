"""Per-layer single-frame decode for compare mode.

The :class:`MasterFrameCache` decodes a *composited* buffer for a
master frame (= the whole stack flattened). Compare mode needs raw
per-layer pixels — A and B independently, no compositing — so we
have a small dedicated entry point that picks the right decode path
per layer kind (image sequence / still / video) without disturbing
the cache.

Pure synchronous calls. The compare overlay calls into this on every
``frame_changed``; image-sequence files are tiny enough that an OIIO
read takes < 5 ms warm and we keep the previous buffer cached on
the :class:`CompareDecoder` instance to avoid re-reading the same
file on idle redraws (= the frame number didn't change).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from img_player.io.reader import FrameReadError, read_frame
from img_player.layers import Layer

log = logging.getLogger(__name__)


@dataclass
class _LastDecode:
    """Tiny one-slot cache per layer id.

    Saves the result of the last successful decode so subsequent
    requests for the same ``(layer_id, source_frame)`` pair return
    instantly. Bigger LRU isn't worth it: compare mode redraws on
    frame change, and the master frame cache already handles bulk
    prefetch for image sequences.
    """

    layer_id: str
    source_frame: int
    arr: np.ndarray


class CompareDecoder:
    """Stateless-ish helper bound to a :class:`VideoSourceManager`
    so video layers can reuse the same PyAV containers as the live
    viewport. Image-sequence layers go straight to OIIO via
    :func:`read_frame`.
    """

    def __init__(self, video_sources) -> None:  # type: ignore[no-untyped-def]
        # ``video_sources`` is the app's VideoSourceManager. We keep
        # it as ``object`` here so the test path can stub a duck-typed
        # mock without importing the real class.
        self._video_sources = video_sources
        # Per-layer single-slot cache.
        self._last: dict[str, _LastDecode] = {}

    # ------------------------------------------------------------------ Public

    def decode(self, layer: Layer, master_frame: int) -> np.ndarray | None:
        """Decode ``layer`` at ``master_frame``. ``None`` when out of range
        or on read failure (caller falls back to whatever else it has)."""
        if not layer.covers(master_frame):
            return None

        if layer.is_video:
            return self._decode_video(layer, master_frame)

        return self._decode_image(layer, master_frame)

    def invalidate(self, layer_id: str | None = None) -> None:
        """Drop cached buffers — call when a layer mutates (offset,
        in/out, channel selection) so the next decode reads fresh.

        ``None`` invalidates everything (used at compare-mode entry
        / exit so we don't leak stale state across sessions)."""
        if layer_id is None:
            self._last.clear()
        else:
            self._last.pop(layer_id, None)

    # ------------------------------------------------------------------ Internals

    def _decode_image(
        self, layer: Layer, master_frame: int,
    ) -> np.ndarray | None:
        source_frame = layer.source_frame_at(master_frame)
        last = self._last.get(layer.id)
        if last is not None and last.source_frame == source_frame:
            return last.arr
        # Resolve the disk path through the layer's sequence.
        path = None
        for fi in layer.sequence.frames:
            if fi.frame_number == source_frame:
                path = fi.path
                break
        if path is None:
            return None
        sel = layer.channel_selection
        channels = list(sel.active.channels) if sel is not None else None
        try:
            arr = read_frame(path, channels=channels, as_half=False)
        except FrameReadError as err:
            log.warning("[compare] decode failed for %s: %s", path, err)
            return None
        self._last[layer.id] = _LastDecode(
            layer_id=layer.id, source_frame=source_frame, arr=arr,
        )
        return arr

    def _decode_video(
        self, layer: Layer, master_frame: int,
    ) -> np.ndarray | None:
        if layer.video_metadata is None:
            return None
        meta = layer.video_metadata
        if meta.fps is None or meta.fps <= 0:
            return None
        source_frame_idx = master_frame - layer.master_start
        t_seconds = source_frame_idx / float(meta.fps)
        try:
            return self._video_sources.decode_at(layer.id, meta.path, t_seconds)
        except Exception:
            log.exception("[compare] video decode failed for %s", layer.id)
            return None
