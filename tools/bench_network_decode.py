"""End-to-end bench: scan headers + decode RGB-only on a real network sequence.

Mirrors what Flick does at scan + scrub time. Three scenarios:

1. SEQUENTIAL header scan — open each file's header in order.
2. PARALLEL header scan — same with ThreadPoolExecutor (N workers).
3. SERIAL RGB-only decode — single-threaded N-frame decode loop.
4. PARALLEL RGB-only decode — N workers, full pool saturation.

Output is a CSV-friendly table so we can compare runs (different N,
local vs network, channel subsets).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import OpenImageIO as oiio


def list_exrs(directory: str, n: int | None = None) -> list[str]:
    files = sorted(Path(directory).glob("*.exr"))
    if n is not None:
        files = files[:n]
    return [str(p) for p in files]


def read_header(path: str) -> tuple[int, int, int]:
    """Return (width, height, nchannels)."""
    inp = oiio.ImageInput.open(path)
    spec = inp.spec()
    out = (spec.width, spec.height, spec.nchannels)
    inp.close()
    return out


def read_rgb(path: str) -> tuple[str, float]:
    """Read first 3 channels (= RGB beauty for most pipelines)."""
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    pixels = inp.read_image(0, 3, oiio.HALF)
    inp.close()
    return (path, time.perf_counter() - t0)


def read_all(path: str) -> tuple[str, float]:
    """Read every channel — what Flick would do without channel
    selection. Used to quantify the gap vs RGB-only."""
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    pixels = inp.read_image(format=oiio.HALF)
    inp.close()
    return (path, time.perf_counter() - t0)


def bench_sequential_headers(paths: list[str]) -> float:
    t0 = time.perf_counter()
    for p in paths:
        read_header(p)
    return time.perf_counter() - t0


def bench_parallel_headers(paths: list[str], workers: int) -> float:
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(read_header, paths):
            pass
    return time.perf_counter() - t0


def bench_serial_decode(paths: list[str], reader=read_rgb) -> tuple[float, list[float]]:
    per_frame: list[float] = []
    t0 = time.perf_counter()
    for p in paths:
        _, dt = reader(p)
        per_frame.append(dt)
    return time.perf_counter() - t0, per_frame


def bench_parallel_decode(
    paths: list[str], workers: int, reader=read_rgb,
) -> tuple[float, list[float]]:
    per_frame: list[float] = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(reader, p) for p in paths]
        for fut in as_completed(futures):
            _, dt = fut.result()
            per_frame.append(dt)
    return time.perf_counter() - t0, per_frame


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--frames", type=int, default=10,
                    help="how many frames to test (default 10)")
    ap.add_argument("--workers", type=int, default=8,
                    help="ThreadPool size (default 8)")
    ap.add_argument("--full", action="store_true",
                    help="also bench full-channel reads (slow)")
    args = ap.parse_args()

    paths = list_exrs(args.directory, n=args.frames)
    print(f"Sequence: {args.directory}")
    print(f"Frames: {len(paths)}  (testing first {args.frames})")

    # Probe one file for context
    w, h, nch = read_header(paths[0])
    print(f"Frame dims: {w}x{h}, channels: {nch}")
    print()
    print("=" * 70)

    # --- Header scan
    print("[1] Sequential header scan")
    t = bench_sequential_headers(paths)
    print(f"    total {t*1000:7.1f} ms  ({t*1000/len(paths):5.1f} ms/file)")

    for w_ in (4, 8, 16, 32):
        if w_ > args.workers:
            break
        t = bench_parallel_headers(paths, w_)
        print(f"[2] Parallel header scan ({w_:2d} workers)")
        print(f"    total {t*1000:7.1f} ms  ({t*1000/len(paths):5.1f} ms/file)")

    print()
    print("=" * 70)

    # --- RGB-only decode (Flick's typical case)
    print("[3] Serial RGB-only decode")
    total, per = bench_serial_decode(paths, reader=read_rgb)
    print(f"    total {total*1000:7.1f} ms  ({total*1000/len(paths):5.1f} ms/file)")
    print(f"    avg/frame: {sum(per)/len(per)*1000:.1f} ms  "
          f"max: {max(per)*1000:.1f}  min: {min(per)*1000:.1f}")

    for w_ in (4, 8, 16, 32):
        if w_ > args.workers:
            break
        total, per = bench_parallel_decode(paths, w_, reader=read_rgb)
        print(f"[4] Parallel RGB-only decode ({w_:2d} workers)")
        print(f"    wall  {total*1000:7.1f} ms  "
              f"effective fps: {len(paths)/total:5.1f}")
        print(f"    per-frame avg {sum(per)/len(per)*1000:.1f} ms "
              f"(workers fight for I/O)")

    if args.full:
        print()
        print("=" * 70)
        print("[5] Serial full-channel decode (all 158)")
        total, per = bench_serial_decode(paths[:3], reader=read_all)
        print(f"    total {total*1000:7.1f} ms  ({total*1000/len(paths[:3]):5.1f} ms/file)")


if __name__ == "__main__":
    main()
