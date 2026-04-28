"""OIIO-backed image sequence writer (PNG / JPG / EXR / TIFF)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from img_player.export.settings import ExportSettings
from img_player.export.writers.base import BaseWriter, ExportWriteError

log = logging.getLogger(__name__)


# Map our format key to the OIIO type the writer should request.
# EXR → half float (matches reader convention). TIFF → 16-bit uint.
# PNG / JPG → 8-bit uint.
_OIIO_FILE_TYPES = {
    "png":  oiio.UINT8,
    "jpg":  oiio.UINT8,
    "exr":  oiio.HALF,
    "tiff": oiio.UINT16,
}

# Map our format key to the dtype the engine should pass us. The
# renderer converts to this before calling write_frame so the
# writer doesn't itself orchestrate dtype conversions.
_OUTPUT_DTYPES = {
    "png":  np.uint8,
    "jpg":  np.uint8,
    "exr":  np.float16,
    "tiff": np.uint16,
}


def output_dtype_for(format_key: str) -> np.dtype:
    """Public helper used by the renderer to know what to convert to."""
    return np.dtype(_OUTPUT_DTYPES.get(format_key, np.uint8))


class ImageSequenceWriter(BaseWriter):
    """Writes one file per frame, named ``<basename>.NNNN.<ext>``.

    The basename mirrors the source sequence's basename so the
    convention stays consistent — pipelines downstream typically
    keyed by basename pattern just keep working.
    """

    def __init__(self, basename: str = "export") -> None:
        self._basename = basename
        self._settings: ExportSettings | None = None
        self._width = 0
        self._height = 0
        self._padding = 4  # min 4 digits, more if the range exceeds 9999
        self._files_written: list[Path] = []
        self._closed = False
        self._aborted = False

    # ------------------------------------------------------------------ Lifecycle

    def open(
        self, settings: ExportSettings, width: int, height: int, fps: float
    ) -> None:
        del fps  # image sequence has no per-stream fps
        if not settings.is_image_sequence:
            raise ExportWriteError(
                f"ImageSequenceWriter cannot handle {settings.format_key!r}"
            )
        self._settings = settings
        self._width = width
        self._height = height
        # Ensure the output dir exists. Callers may pre-create it
        # but a missing dir shouldn't be fatal — we own this path.
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        # Pad width: enough digits for the largest frame number we'll
        # write. Range = settings.start_frame + settings.total_frames - 1.
        last = settings.start_frame + settings.total_frames - 1
        self._padding = max(4, len(str(max(last, 0))))

    def write_frame(self, arr: np.ndarray, frame_idx: int) -> None:
        if self._settings is None:
            raise ExportWriteError("write_frame() before open()")
        fmt = self._settings.fmt
        ext = fmt.extension  # ".png" etc.
        # Filename is settings.start_frame + the export-relative index.
        # The engine passes ``frame_idx`` as 0-based, so the file
        # number is start_frame + frame_idx.
        file_number = self._settings.start_frame + frame_idx
        filename = f"{self._basename}.{file_number:0{self._padding}d}{ext}"
        out_path = self._settings.output_dir / filename
        self._write_via_oiio(out_path, arr)
        self._files_written.append(out_path)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # OIIO closes per-write — nothing batched at our level.
        log.info(
            "[export] image-seq writer closed: %d files in %s",
            len(self._files_written),
            self._settings.output_dir if self._settings else "<unset>",
        )

    def abort(self) -> None:
        """Delete every file we wrote so the user doesn't end up
        with a half-finished sequence on disk."""
        self._aborted = True
        for p in self._files_written:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                log.exception("[export] failed to remove %s on abort", p)
        self._files_written.clear()
        self.close()

    def output_path(self) -> Path:
        if self._settings is None:
            return Path(".")
        return self._settings.output_dir

    # ------------------------------------------------------------------ Internals

    def _write_via_oiio(self, path: Path, arr: np.ndarray) -> None:
        if self._settings is None:
            raise ExportWriteError("internal: _settings missing")
        fmt = self._settings.fmt
        if arr.shape[0] != self._height or arr.shape[1] != self._width:
            raise ExportWriteError(
                f"frame shape {arr.shape[:2]} mismatches expected "
                f"{(self._height, self._width)}"
            )
        # Strip alpha for JPG (no alpha support).
        if not fmt.supports_alpha and arr.ndim == 3 and arr.shape[2] >= 4:
            arr = arr[..., :3]
        if arr.ndim != 3:
            raise ExportWriteError(f"expected HxWxC array, got shape {arr.shape}")
        nchannels = arr.shape[2]
        # OIIO wants contiguous memory.
        arr = np.ascontiguousarray(arr)

        spec = oiio.ImageSpec(
            self._width, self._height, nchannels, _OIIO_FILE_TYPES[fmt.key]
        )
        # Channel names: stick to the conventional R/G/B/A. OIIO uses
        # this for the EXR multi-layer convention; PNG/JPG/TIFF
        # tolerate it just fine.
        ch_names = ("R", "G", "B", "A")[:nchannels]
        spec.channelnames = list(ch_names)

        if fmt.key == "jpg":
            spec.attribute("CompressionQuality", int(self._settings.jpg_quality))
        elif fmt.key == "exr":
            spec.attribute("compression", self._settings.exr_compression)
            # Tag the colorspace metadata so downstream tools know
            # whether this EXR is linear (passthrough) or display-baked.
            cs = "Linear" if not self._settings.apply_display_transform else "sRGB"
            spec.attribute("oiio:ColorSpace", cs)
        elif fmt.key == "tiff":
            # LZW is the standard archival default — losslessly small.
            spec.attribute("compression", "lzw")

        out = oiio.ImageOutput.create(str(path))
        if out is None:
            raise ExportWriteError(
                f"OIIO cannot create writer for {path} ({oiio.geterror()})"
            )
        try:
            if not out.open(str(path), spec):
                raise ExportWriteError(
                    f"OIIO cannot open {path} for write ({out.geterror()})"
                )
            if not out.write_image(arr):
                raise ExportWriteError(
                    f"OIIO write_image failed for {path} ({out.geterror()})"
                )
        finally:
            out.close()
