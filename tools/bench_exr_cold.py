"""Cold-network bench: each frame read ONCE, fresh (no OS cache reuse).

Alternates files so neither method benefits from the other's cache.
Compares the current network path (PyOpenEXR full, 48 channels) vs
OIIO RGBA-subset, on real cold SMB reads.

Run: python tools/bench_exr_cold.py "<dir>" 1001 1012
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import OpenEXR
import OpenImageIO as oiio


def pyopenexr_full(path):
    with OpenEXR.File(path) as f:
        ch = f.parts[0].channels
        return np.asarray(ch["RGBA"].pixels)


def oiio_subset(path):
    inp = oiio.ImageInput.open(path)
    try:
        return np.asarray(inp.read_image(0, 4, oiio.HALF))
    finally:
        inp.close()


def main() -> None:
    d = Path(sys.argv[1])
    lo, hi = int(sys.argv[2]), int(sys.argv[3])
    frames = list(range(lo, hi + 1))
    # find the naming pattern
    sample = next(d.glob("*.exr"))
    stem = sample.stem.rsplit(".", 1)[0]  # CHARS.1024 -> CHARS
    ext = sample.suffix

    pyx, oii = [], []
    for i, fr in enumerate(frames):
        p = str(d / f"{stem}.{fr}{ext}")
        if not Path(p).exists():
            continue
        # Alternate which method reads each fresh file so the OS cache
        # from method A doesn't warm method B's file.
        if i % 2 == 0:
            t0 = time.perf_counter(); pyopenexr_full(p); pyx.append(time.perf_counter() - t0)
        else:
            t0 = time.perf_counter(); oiio_subset(p); oii.append(time.perf_counter() - t0)

    def stat(name, xs):
        if not xs:
            print(f"{name}: no samples"); return
        xs = sorted(xs)
        print(f"{name}: n={len(xs)} min={xs[0]*1000:.0f} "
              f"med={xs[len(xs)//2]*1000:.0f} max={xs[-1]*1000:.0f} ms")

    print("COLD (each file read once, fresh):")
    stat("  PyOpenEXR full (48ch, current net path)", pyx)
    stat("  OIIO subset RGBA (0..4)               ", oii)


if __name__ == "__main__":
    main()
