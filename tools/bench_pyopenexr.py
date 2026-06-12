"""Compare reading strategies via PyOpenEXR (skip OIIO for .exr).

PyOpenEXR 3.x exposes:
- OpenEXR.File(path)       : direct path read (= equivalent of OIIO)
- OpenEXR.File(Bytes(data)): read from a byte buffer (mmap or read())

Tests three strategies + the existing OIIO baseline:
1. OIIO read_image (current Flick path)
2. PyOpenEXR via path
3. PyOpenEXR via bytes (open().read())
4. PyOpenEXR via mmap (mmap module)
"""

from __future__ import annotations

import argparse
import mmap
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import OpenEXR
import OpenImageIO as oiio


def read_oiio(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    spec = inp.spec()
    nch = min(3, spec.nchannels)
    pix = inp.read_image(0, nch, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0, pix.shape


def read_exr_path(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    with OpenEXR.File(path) as f:
        # Get only first channels (R/G/B). The new API uses channels()
        # to return a dict; we pluck R/G/B.
        chans = f.channels()
        # Just pull the data attrs we need; shape inferred from R.
        r = np.asarray(chans["R"].pixels, dtype=np.float16)
    return time.perf_counter() - t0, r.shape


def read_exr_bytes(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    with open(path, "rb") as fh:
        data = fh.read()
    # PyOpenEXR can read from a bytes-like buffer via Bytes().
    with OpenEXR.File(OpenEXR.Bytes(data)) as f:
        chans = f.channels()
        r = np.asarray(chans["R"].pixels, dtype=np.float16)
    return time.perf_counter() - t0, r.shape


def read_exr_mmap(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    with open(path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            # PyOpenEXR.Bytes() accepts bytes-like; mmap is bytes-like.
            with OpenEXR.File(OpenEXR.Bytes(bytes(mm))) as f:
                chans = f.channels()
                r = np.asarray(chans["R"].pixels, dtype=np.float16)
        finally:
            mm.close()
    return time.perf_counter() - t0, r.shape


STRATS = {
    "oiio    ": read_oiio,
    "pyexr-p ": read_exr_path,
    "pyexr-b ": read_exr_bytes,
    "pyexr-mm": read_exr_mmap,
}


def time_one(strat: str, path: str) -> tuple[float, tuple]:
    return STRATS[strat](path)


def serial(paths, strat):
    times = []
    t0 = time.perf_counter()
    for p in paths:
        dt, _ = time_one(strat, p)
        times.append(dt)
    return time.perf_counter() - t0, times


def parallel(paths, strat, workers):
    times = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(time_one, strat, p) for p in paths]
        for f in as_completed(futs):
            dt, _ = f.result()
            times.append(dt)
    return time.perf_counter() - t0, times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--frames-per-test", type=int, default=4)
    args = ap.parse_args()

    paths = sorted(Path(args.directory).glob("*.exr"))
    paths = [str(p) for p in paths]
    print(f"PyOpenEXR version: {OpenEXR.OPENEXR_VERSION}")
    print(f"OIIO version     : {oiio.__version__}")
    print(f"Sequence has {len(paths)} files; using disjoint slices of "
          f"{args.frames_per_test} per test (cold reads)")
    print()

    n = args.frames_per_test
    tests = []
    # Serial single-worker
    for i, strat in enumerate(STRATS):
        tests.append((f"{strat} serial 1w   ",
                      paths[i*n:(i+1)*n], strat, 1))
    # Parallel 4w
    offset = len(STRATS) * n
    for i, strat in enumerate(STRATS):
        tests.append((f"{strat} parallel 4w ",
                      paths[offset + i*n:offset + (i+1)*n],
                      strat, 4))
    # Parallel 8w
    offset = 2 * len(STRATS) * n
    for i, strat in enumerate(STRATS):
        tests.append((f"{strat} parallel 8w ",
                      paths[offset + i*n:offset + (i+1)*n],
                      strat, 8))

    print(f"{'Test':<26}  {'wall ms':>8}  {'per-frame ms':>12}  {'fps':>5}")
    print("-" * 62)
    for label, slice_paths, strat, w in tests:
        if not slice_paths or len(slice_paths) < n:
            print(f"{label}  (not enough frames, skipping)")
            continue
        try:
            if w == 1:
                wall, times = serial(slice_paths, strat)
            else:
                wall, times = parallel(slice_paths, strat, w)
        except Exception as e:
            print(f"{label}  FAILED: {type(e).__name__}: {e}")
            continue
        per = sum(times)/len(times)*1000
        fps = len(slice_paths)/wall
        print(f"{label}  {wall*1000:>8.0f}  {per:>12.0f}  {fps:>5.1f}")


if __name__ == "__main__":
    main()
