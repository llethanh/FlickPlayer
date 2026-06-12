"""Compare OIIO read strategies for cold network EXR.

Three strategies on disjoint cold frames:
1. Baseline    : oiio.ImageInput.open(path) — current Flick path
2. Bytes+Proxy : open(path,'rb').read() → IOMemReader → OIIO
3. Mmap+Proxy  : mmap(path) → IOMemReader → OIIO

Mirrors OpenRV's FileStream mmap pattern but in Python via OIIO's
IOProxy. If win is real (> 1.5x cold), we wire it into read_frame.
"""

from __future__ import annotations

import argparse
import mmap
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import OpenImageIO as oiio


# ---- Probe what IOProxy APIs OIIO 3.1.14 actually exposes ---------------
def _probe_oiio_api() -> dict[str, bool]:
    api: dict[str, bool] = {}
    api["IOMemReader"]   = hasattr(oiio, "IOMemReader")
    api["IOMemoryReader"] = hasattr(oiio, "IOMemoryReader")
    api["IOMemoryStream"] = hasattr(oiio, "IOMemoryStream")
    api["ImageBuf_from_buffer"] = hasattr(oiio.ImageBuf, "set_buffer")
    return api


def read_baseline(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    spec = inp.spec()
    nch = min(3, spec.nchannels)
    pixels = inp.read_image(0, nch, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0, pixels.shape


def read_via_bytes(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        data = f.read()
    # Now feed to OIIO via the IOProxy in-memory path.
    proxy_cls = getattr(oiio, "IOMemReader", None) or getattr(
        oiio, "IOMemoryReader", None,
    )
    if proxy_cls is None:
        raise RuntimeError(
            "OIIO build doesn't expose IOMemReader/IOMemoryReader",
        )
    proxy = proxy_cls(data)
    # OIIO needs a hint for which plugin to use when the path is empty
    # — pass the filename so it picks .exr.
    inp = oiio.ImageInput.open(path, None, proxy)
    if inp is None:
        raise RuntimeError(
            f"OIIO failed to open via proxy: {oiio.geterror()}",
        )
    spec = inp.spec()
    nch = min(3, spec.nchannels)
    pixels = inp.read_image(0, nch, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0, pixels.shape


def read_via_mmap(path: str) -> tuple[float, tuple]:
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        # Touch ALL pages to force OS to stream the whole file via
        # sequential SMB transfer (= best network throughput). On
        # Windows this triggers the SMB client to issue ReadAhead
        # requests, hiding latency.
        # We could also memoryview the mmap and pass to IOMemReader.
        proxy_cls = getattr(oiio, "IOMemReader", None) or getattr(
            oiio, "IOMemoryReader", None,
        )
        if proxy_cls is None:
            raise RuntimeError("OIIO build doesn't expose IOMemReader")
        # IOMemReader accepts bytes-like. mmap is bytes-like.
        proxy = proxy_cls(mm)
        inp = oiio.ImageInput.open(path, None, proxy)
        if inp is None:
            raise RuntimeError(f"OIIO mmap proxy open failed: {oiio.geterror()}")
        spec = inp.spec()
        nch = min(3, spec.nchannels)
        pixels = inp.read_image(0, nch, oiio.HALF)
        inp.close()
        mm.close()
    return time.perf_counter() - t0, pixels.shape


def time_one(strat: str, path: str) -> tuple[float, tuple]:
    if strat == "baseline":
        return read_baseline(path)
    if strat == "bytes":
        return read_via_bytes(path)
    if strat == "mmap":
        return read_via_mmap(path)
    raise ValueError(strat)


def serial(paths: list[str], strat: str) -> tuple[float, list[float]]:
    times = []
    t0 = time.perf_counter()
    for p in paths:
        dt, _ = time_one(strat, p)
        times.append(dt)
    return time.perf_counter() - t0, times


def parallel(paths: list[str], strat: str, workers: int) -> tuple[float, list[float]]:
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
    ap.add_argument("--frames-per-test", type=int, default=6)
    args = ap.parse_args()

    api = _probe_oiio_api()
    print("OIIO API probe:")
    for k, v in api.items():
        print(f"  {k}: {v}")
    print()

    all_paths = sorted(Path(args.directory).glob("*.exr"))
    paths = [str(p) for p in all_paths]
    print(f"Sequence has {len(paths)} files, {args.frames_per_test} per test")
    print()

    n = args.frames_per_test
    tests = [
        ("baseline serial 1w   ", paths[0*n:1*n], "baseline", 1),
        ("bytes    serial 1w   ", paths[1*n:2*n], "bytes",    1),
        ("mmap     serial 1w   ", paths[2*n:3*n], "mmap",     1),
        ("baseline parallel 4w ", paths[3*n:4*n], "baseline", 4),
        ("bytes    parallel 4w ", paths[4*n:5*n], "bytes",    4),
        ("mmap     parallel 4w ", paths[5*n:6*n], "mmap",     4),
        ("baseline parallel 8w ", paths[6*n:7*n], "baseline", 8),
        ("bytes    parallel 8w ", paths[7*n:8*n], "bytes",    8),
        ("mmap     parallel 8w ", paths[8*n:9*n], "mmap",     8),
    ]

    print(f"{'Test':<24}  {'wall ms':>8}  {'per-frame ms':>12}  {'fps':>5}")
    print("-" * 60)
    for label, slice_paths, strat, w in tests:
        if not slice_paths:
            print(f"{label}  (not enough frames, skipping)")
            continue
        try:
            if w == 1:
                wall, times = serial(slice_paths, strat)
            else:
                wall, times = parallel(slice_paths, strat, w)
        except Exception as e:
            print(f"{label}  FAILED: {e}")
            continue
        per = sum(times)/len(times)*1000
        fps = len(slice_paths)/wall
        print(f"{label}  {wall*1000:>8.0f}  {per:>12.0f}  {fps:>5.1f}")


if __name__ == "__main__":
    main()
