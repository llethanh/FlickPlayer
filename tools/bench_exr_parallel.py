"""Does EXR decode scale across worker threads, or is it GIL-bound?

Reads N frames with a ThreadPoolExecutor and reports aggregate
frames/sec for each method. If PyOpenEXR doesn't scale with threads
(holds the GIL during decompress) but OIIO does (releases it), that's
the real bottleneck behind "Flick is 3x slower than OpenRV".

Run: python tools/bench_exr_parallel.py "<dir>" 1001 1020 12
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import OpenEXR
import OpenImageIO as oiio


def pyopenexr_full(path):
    with OpenEXR.File(path) as f:
        return np.asarray(f.parts[0].channels["RGBA"].pixels)


def oiio_subset(path):
    inp = oiio.ImageInput.open(path)
    try:
        return np.asarray(inp.read_image(0, 4, oiio.HALF))
    finally:
        inp.close()


def run(name, fn, paths, threads):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(fn, paths))
    dt = time.perf_counter() - t0
    print(f"{name}: {len(paths)} frames / {threads} threads = "
          f"{dt:.1f}s  -> {len(paths)/dt:.1f} frames/s")
    return dt


def main() -> None:
    d = Path(sys.argv[1])
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 12
    sample = next(d.glob("*.exr"))
    stem = sample.stem.rsplit(".", 1)[0]
    ext = sample.suffix
    paths = [str(d / f"{stem}.{fr}{ext}") for fr in range(lo, hi + 1)]
    paths = [p for p in paths if Path(p).exists()]
    print(f"{len(paths)} frames, {threads} threads\n")

    # 1-thread baseline first (single-frame cost), then parallel.
    print("--- 1 thread (baseline) ---")
    run("PyOpenEXR full ", pyopenexr_full, paths, 1)
    run("OIIO subset    ", oiio_subset, paths, 1)
    print(f"\n--- {threads} threads (does it scale?) ---")
    run("PyOpenEXR full ", pyopenexr_full, paths, threads)
    run("OIIO subset    ", oiio_subset, paths, threads)


if __name__ == "__main__":
    main()
