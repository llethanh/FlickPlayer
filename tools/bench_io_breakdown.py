"""Break down where the 770 ms/frame actually goes.

For one cold network frame, time separately:
1. Pure ``open() + f.read()`` (raw SMB bulk transfer, no decode)
2. OIIO open header (light parse only)
3. OIIO full read 3 channels (decode + I/O combined)

If (1) is much shorter than (3), I/O isn't the bottleneck — decode is.
If (1) is comparable to (3), I/O dominates and overlapped async reads
(à la OpenRV) would help.

Usage: bench_io_breakdown.py <directory> [--skip-first N]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import OpenImageIO as oiio


def time_pure_read(path: str) -> tuple[float, int]:
    """Just slurp the file. Single big f.read() = optimal SMB pattern."""
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        data = f.read()
    return time.perf_counter() - t0, len(data)


def time_oiio_open(path: str) -> tuple[float, dict]:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    spec = inp.spec()
    dims = {
        "w": spec.width,
        "h": spec.height,
        "ch": spec.nchannels,
        "comp": spec.get_string_attribute("compression"),
    }
    inp.close()
    return time.perf_counter() - t0, dims


def time_oiio_full_rgb(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    pix = inp.read_image(0, 3, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0, pix.shape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--skip-first", type=int, default=20,
                    help="skip the first N frames (avoid warm cache from prior bench runs)")
    args = ap.parse_args()

    all_paths = sorted(Path(args.directory).glob("*.exr"))
    paths = [str(p) for p in all_paths[args.skip_first:]]
    if len(paths) < 3:
        print("need at least 3 cold frames")
        sys.exit(1)

    print(f"{'phase':<28}  {'ms':>7}  {'MB/s':>8}  {'detail':<30}")
    print("-" * 76)

    # ---- phase 1: pure bulk read ----
    for i, p in enumerate(paths[:3]):
        dt, n = time_pure_read(p)
        mb = n / 1024 / 1024
        rate = mb / dt
        print(f"pure read #{i:<2}                  {dt*1000:>7.0f}  "
              f"{rate:>7.1f}  {mb:.0f} MB total")

    # ---- phase 2: header only ----
    for i, p in enumerate(paths[3:6]):
        dt, info = time_oiio_open(p)
        print(f"oiio header-only #{i:<2}          {dt*1000:>7.0f}  "
              f"{'-':>8}  {info['w']}x{info['h']} {info['ch']}ch {info['comp']}")

    # ---- phase 3: full 3-channel decode via OIIO ----
    for i, p in enumerate(paths[6:9]):
        dt, sh = time_oiio_full_rgb(p)
        print(f"oiio full 3-ch decode #{i:<2}      {dt*1000:>7.0f}  "
              f"{'-':>8}  shape={sh}")


if __name__ == "__main__":
    main()
