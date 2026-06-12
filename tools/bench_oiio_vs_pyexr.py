"""Final comparison: OIIO 3-channels read vs PyOpenEXR full file read.

PyOpenEXR returns multi-component channels (e.g. ``RGBA`` is a single
key with a (H, W, 4) array). On AOV-heavy EXRs, even decoding ALL the
parts is faster than OIIO selectively decoding 3 channels — because
the OpenEXR C++ lib's read path is more efficient than OIIO's plugin
wrapper.

Cold reads via disjoint slices, 6 strategies × 3 worker configs.
"""

from __future__ import annotations
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import OpenEXR
import OpenImageIO as oiio


def read_oiio_rgb(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    pix = inp.read_image(0, 3, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0, pix.shape


def read_pyexr_full(path: str) -> tuple[float, tuple]:
    """PyOpenEXR full decode + extract RGBA plane.

    PyOpenEXR's channel keys are grouped (``"RGBA"`` is one key with
    a (H, W, 4) ndarray). We just need RGBA for display so we read
    everything (cheap with this lib) then keep just that key.
    """
    t0 = time.perf_counter()
    with OpenEXR.File(path) as f:
        chans = f.parts[0].channels
        rgba_key = "RGBA" if "RGBA" in chans else next(
            (k for k in chans if k.startswith("RGB")), None,
        )
        if rgba_key is None:
            raise RuntimeError(f"No RGB-like channel in {path}")
        pix = np.asarray(chans[rgba_key].pixels, dtype=np.float16)
    return time.perf_counter() - t0, pix.shape


STRATS = {
    "oiio rgb 3-ch  ": read_oiio_rgb,
    "pyexr full read": read_pyexr_full,
}


def time_one(strat: str, path: str):
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
    ap.add_argument("--skip", type=int, default=50,
                    help="skip first N frames (avoid warm cache)")
    args = ap.parse_args()

    print(f"PyOpenEXR {OpenEXR.OPENEXR_VERSION}, OIIO {oiio.__version__}")

    paths = [str(p) for p in sorted(Path(args.directory).glob("*.exr"))[args.skip:]]
    print(f"Cold frames available: {len(paths)}; using {args.frames_per_test} per test")
    print()

    n = args.frames_per_test
    tests = []
    idx = 0
    for w in (1, 4, 8):
        for strat in STRATS:
            tests.append((f"{strat} {w}w   ", paths[idx*n:(idx+1)*n], strat, w))
            idx += 1

    print(f"{'Test':<26}  {'wall ms':>8}  {'per-frame':>10}  {'fps':>5}  {'shape':<20}")
    print("-" * 76)
    for label, slice_paths, strat, w in tests:
        if not slice_paths or len(slice_paths) < n:
            print(f"{label} (not enough frames)")
            continue
        try:
            if w == 1:
                wall, times = serial(slice_paths, strat)
            else:
                wall, times = parallel(slice_paths, strat, w)
            # peek a shape
            _, sh = time_one(strat, slice_paths[0])
        except Exception as e:
            print(f"{label} FAILED: {type(e).__name__}: {e}")
            continue
        per = sum(times)/len(times)*1000
        fps = len(slice_paths)/wall
        print(f"{label}  {wall*1000:>8.0f}  {per:>10.0f}  {fps:>5.1f}  {sh}")


if __name__ == "__main__":
    main()
