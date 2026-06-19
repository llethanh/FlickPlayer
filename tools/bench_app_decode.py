"""End-to-end: time the REAL app decode path on a full sequence.

Drives img_player.io.reader.read_frame (the exact code the cache
workers call) with channels=RGBA across all frames in a 12-worker
pool — and separately times pure file I/O (np.fromfile) so we can
tell decode-bound from I/O-bound.

Run: python tools/bench_app_decode.py "<dir>" [threads]
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from img_player.io.reader import read_frame  # noqa: E402


def main() -> None:
    d = Path(sys.argv[1])
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    files = sorted(d.glob("*.exr"))
    paths = [str(p) for p in files]
    total_bytes = sum(p.stat().st_size for p in files)
    print(f"{len(paths)} frames, {total_bytes/1024**3:.1f} GB total, "
          f"{threads} threads\n")

    # 1) Pure I/O: read raw bytes, no decode. The transfer ceiling.
    def raw(p):
        return np.fromfile(p, dtype=np.uint8).nbytes

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        got = sum(ex.map(raw, paths))
    dt_io = time.perf_counter() - t0
    print(f"raw I/O (np.fromfile)     : {dt_io:5.1f}s  "
          f"{got/1024**3/dt_io:5.2f} GB/s")

    # 2) Real app decode path: read_frame, RGBA, contiguous subset.
    def dec(p):
        return read_frame(p, channels=["R", "G", "B", "A"], as_half=True).shape

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(dec, paths))
    dt_dec = time.perf_counter() - t0
    print(f"read_frame RGBA (app path): {dt_dec:5.1f}s  "
          f"{len(paths)/dt_dec:5.1f} frames/s")

    print(f"\ndecode overhead over raw I/O: {dt_dec - dt_io:+.1f}s")


if __name__ == "__main__":
    main()
