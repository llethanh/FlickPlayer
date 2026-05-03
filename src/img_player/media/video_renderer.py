"""Owns open :class:`VideoSource` decoders keyed by layer id.

The renderer-side counterpart to ``Layer.from_video``: when a video
layer is added to the stack the manager opens its decoder lazily on
first frame access; when the layer is removed the manager closes the
file handle so we don't leak across session loads.

Decoding currently runs **synchronously on the Qt main thread**.
That's the simplest correct path for the v1 video MVP — keyframe-near
decodes (the common case during play / scrub) take well under a
display frame on modern hardware. Moving to a worker thread is a
straightforward later swap (the manager already encapsulates the
sources behind a thin API; only ``decode_at`` would change to enqueue
+ wait on a future).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from img_player.media.video_source import VideoSource

log = logging.getLogger(__name__)


class VideoSourceManager:
    """Pool of open :class:`VideoSource` decoders, keyed by layer id.

    Not thread-safe — single-threaded by design (see module docstring).
    Opens are lazy: the first ``decode_at`` for a layer creates the
    underlying source.
    """

    def __init__(self) -> None:
        self._sources: dict[str, VideoSource] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def get_or_open(self, layer_id: str, path: Path) -> VideoSource:
        """Return the cached source for ``layer_id``, opening it if
        absent. Distinct ``layer_id``s map to distinct sources even if
        they point at the same file — duplicating the decoder makes
        per-layer scrub / play state independent (no contention on the
        seek cursor)."""
        src = self._sources.get(layer_id)
        if src is None:
            src = VideoSource(path)
            self._sources[layer_id] = src
        return src

    def close(self, layer_id: str) -> None:
        """Close the source for ``layer_id`` if any. No-op if absent."""
        src = self._sources.pop(layer_id, None)
        if src is not None:
            try:
                src.close()
            except Exception:
                log.exception("error closing VideoSource for layer %s", layer_id)

    def shutdown(self) -> None:
        """Close every open source. Called on app exit / session swap."""
        for layer_id in list(self._sources.keys()):
            self.close(layer_id)

    # ------------------------------------------------------------------
    # Decode + format conversion
    # ------------------------------------------------------------------

    def decode_at(
        self, layer_id: str, path: Path, t_seconds: float,
    ) -> np.ndarray:
        """Return the frame at time ``t`` as ``(H, W, 4) float32`` RGBA.

        The viewport's ``set_frame`` accepts HxWx3 or HxWx4 in float
        precision; we pad to RGBA1 so the OCIO + alpha-composite
        pipeline downstream doesn't have to special-case 3-channel
        sources. uint8 → float32 conversion divides by 255 so the
        display path treats the values as already-normalised display
        colour (no OCIO input transform applied for now — see the
        v1 plan; the proper colour-managed path arrives once we
        surface the FFmpeg color-primaries / transfer enum at the
        OCIO input picker).
        """
        src = self.get_or_open(layer_id, path)
        rgb_u8 = src.frame_at_time(t_seconds)
        # uint8 → float32 normalised. Use ``ascontiguousarray`` so
        # the GL upload path's ``np.ascontiguousarray`` no-ops.
        rgb = rgb_u8.astype(np.float32, copy=False) * (1.0 / 255.0)
        h, w, _ = rgb.shape
        rgba = np.empty((h, w, 4), dtype=np.float32)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 1.0
        return rgba
