"""Find the SMB read-throughput ceiling vs thread count + read size.

The full-sequence load is I/O-bound (decode is free on top), so the
only lever is reading the 17.5 GB faster. This sweeps concurrency and
buffer size to see what saturates the link.

Run: python tools/bench_smb_throughput.py "<dir>"
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def read_all(p, bufsize):
    n = 0
    with open(p, "rb", buffering=0) as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            n += len(b)
    return n


def sweep(paths, total_gb, threads, bufsize):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as ex:
        list(ex.map(lambda p: read_all(p, bufsize), paths))
    dt = time.perf_counter() - t0
    print(f"  threads={threads:2d}  buf={bufsize//1024//1024:2d}MB  "
          f"{dt:5.1f}s  {total_gb/dt:5.2f} GB/s")


def main() -> None:
    d = Path(sys.argv[1])
    files = sorted(d.glob("*.exr"))
    paths = [str(p) for p in files]
    total_gb = sum(p.stat().st_size for p in files) / 1024**3
    print(f"{len(paths)} frames, {total_gb:.1f} GB\n")

    MB = 1024 * 1024
    print("thread-count sweep (8 MB reads):")
    for t in (8, 12, 24, 48):
        sweep(paths, total_gb, t, 8 * MB)
    print("\nbuffer-size sweep (24 threads):")
    for buf in (1 * MB, 8 * MB, 32 * MB):
        sweep(paths, total_gb, 24, buf)


if __name__ == "__main__":
    main()
