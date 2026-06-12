"""Strictly cold benchmark — each test uses DIFFERENT frames so the
OS / SMB client cache never helps.

This is the honest measurement of cold-network EXR decode. Previous
benchmarks ran multiple tests on the same files, so the second test
benefited from page cache and looked artificially fast.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.io.reader import configure_oiio, read_frame


def time_read(path, channels):
    t0 = time.perf_counter()
    arr = read_frame(path, channels=channels, as_half=True)
    return time.perf_counter() - t0, arr.shape


def serial(paths, channels):
    times, shape = [], None
    t0 = time.perf_counter()
    for p in paths:
        dt, sh = time_read(p, channels)
        times.append(dt)
        shape = sh
    return time.perf_counter() - t0, times, shape


def parallel(paths, workers, channels):
    times, shape = [], None
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(time_read, p, channels) for p in paths]
        for fut in as_completed(futures):
            dt, sh = fut.result()
            times.append(dt)
            shape = sh
    return time.perf_counter() - t0, times, shape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--frames-per-test", type=int, default=8,
                    help="how many frames each test consumes")
    args = ap.parse_args()

    configure_oiio(None)
    all_paths = sorted(Path(args.directory).glob("*.exr"))
    paths = [str(p) for p in all_paths]
    print(f"Sequence has {len(paths)} files; using {args.frames_per_test} per test")
    print()

    # Allocate disjoint slices for each test so the OS cache never
    # carries over.
    n = args.frames_per_test
    tests = [
        ("RGBA serial 1 worker      ",
         paths[0*n:(0+1)*n], ["R","G","B","A"],"serial",  1),
        ("RGB  serial 1 worker      ",
         paths[1*n:(1+1)*n], ["R","G","B"],    "serial",  1),
        ("RGBA parallel 4 workers   ",
         paths[2*n:(2+1)*n], ["R","G","B","A"],"par",     4),
        ("RGB  parallel 4 workers   ",
         paths[3*n:(3+1)*n], ["R","G","B"],    "par",     4),
        ("RGBA parallel 8 workers   ",
         paths[4*n:(4+1)*n], ["R","G","B","A"],"par",     8),
        ("RGB  parallel 8 workers   ",
         paths[5*n:(5+1)*n], ["R","G","B"],    "par",     8),
    ]

    print(f"{'Test':<28}  {'wall ms':>10}  {'per-frame':>12}  {'fps':>6}  {'shape':>20}")
    print("-" * 90)
    for label, slice_paths, ch, mode, w in tests:
        if not slice_paths:
            print(f"{label}  (not enough frames, skipping)")
            continue
        if mode == "serial":
            wall, times, shape = serial(slice_paths, ch)
        else:
            wall, times, shape = parallel(slice_paths, w, ch)
        per = sum(times)/len(times)*1000
        fps = len(slice_paths)/wall
        print(f"{label}  {wall*1000:>10.0f}  {per:>11.0f}   {fps:>5.1f}  {str(shape):>20}")


if __name__ == "__main__":
    main()
