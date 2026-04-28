"""Writer backends for the export feature.

Two flavours:

* :class:`ImageSequenceWriter` — file-per-frame via OpenImageIO.
* :class:`VideoWriter` — single-file container via PyAV (FFmpeg).

Both implement the :class:`BaseWriter` ABC so the engine doesn't
care which one it has.
"""

from __future__ import annotations

from img_player.export.writers.base import BaseWriter, ExportWriteError
from img_player.export.writers.image_seq import ImageSequenceWriter
from img_player.export.writers.video import VideoWriter, build_writer

__all__ = (
    "BaseWriter",
    "ExportWriteError",
    "ImageSequenceWriter",
    "VideoWriter",
    "build_writer",
)
