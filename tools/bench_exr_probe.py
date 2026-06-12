"""Probe a single EXR's header + measure read times for the bench."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import OpenImageIO as oiio


def probe_one(path: str) -> None:
    print(f"=== {Path(path).name} ===")

    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    t_open = time.perf_counter() - t0
    print(f"  Header open: {t_open*1000:6.1f} ms")

    spec = inp.spec()
    print(f"  Resolution : {spec.width} x {spec.height}")
    print(f"  Channels   : {spec.nchannels}")
    compression = spec.get_string_attribute("compression") or "<none>"
    print(f"  Compression: {compression}")
    print(f"  Format     : {spec.format}")
    print(f"  First 8 ch : {spec.channelnames[:8]}")
    if spec.nchannels > 8:
        print(f"  Last 4 ch  : {spec.channelnames[-4:]}")

    # Full decode (all channels)
    t0 = time.perf_counter()
    pixels = inp.read_image(format=oiio.HALF)
    t_full = time.perf_counter() - t0
    print(f"  Full read (all channels, HALF): {t_full*1000:6.1f} ms")
    print(f"  Pixels shape: {pixels.shape}, dtype: {pixels.dtype}")

    inp.close()

    # RGB-only read (channels 0..2)
    t0 = time.perf_counter()
    inp2 = oiio.ImageInput.open(path)
    rgb = inp2.read_image(0, 3, oiio.HALF)
    t_rgb = time.perf_counter() - t0
    inp2.close()
    print(f"  RGB-only read (3 channels, HALF): {t_rgb*1000:6.1f} ms")
    print(f"  Speedup vs full: {t_full / t_rgb:.2f}x")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: bench_exr_probe.py <path/to/file.exr>")
        sys.exit(1)
    probe_one(sys.argv[1])
