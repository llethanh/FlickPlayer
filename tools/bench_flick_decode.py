"""Benchmark Flick's read_frame() directly — apples-to-apples with the
pure-OIIO bench so we can see if Flick has overhead per frame.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure Flick's src/ is importable.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.io.reader import configure_oiio, read_frame, read_header


def list_exrs(directory: str, n: int | None = None) -> list[str]:
    files = sorted(Path(directory).glob("*.exr"))
    if n is not None:
        files = files[:n]
    return [str(p) for p in files]


def time_read(path: str, channels=None, as_half: bool = True) -> tuple[str, float, tuple]:
    t0 = time.perf_counter()
    arr = read_frame(path, channels=channels, as_half=as_half)
    dt = time.perf_counter() - t0
    return (path, dt, arr.shape)


def bench_serial(paths, channels=None, label="default") -> tuple[float, list[float], tuple]:
    per_frame = []
    shape = None
    t0 = time.perf_counter()
    for p in paths:
        _, dt, sh = time_read(p, channels=channels)
        per_frame.append(dt)
        shape = sh
    return time.perf_counter() - t0, per_frame, shape


def bench_parallel(paths, workers, channels=None) -> tuple[float, list[float], tuple]:
    per_frame = []
    shape = None
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(time_read, p, channels) for p in paths]
        for fut in as_completed(futures):
            _, dt, sh = fut.result()
            per_frame.append(dt)
            shape = sh
    return time.perf_counter() - t0, per_frame, shape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--oiio-threads", type=int, default=None,
                    help="OIIO global thread pool size (default = cpu_count())")
    args = ap.parse_args()

    n_oiio = configure_oiio(args.oiio_threads)
    print(f"OIIO threads attribute: {n_oiio}")

    paths = list_exrs(args.directory, n=args.frames)
    print(f"Sequence: {args.directory}")
    print(f"Frames: {len(paths)}")

    # Probe one
    spec = read_header(paths[0])
    print(f"Frame dims: {spec.width}x{spec.height}, channels: {spec.nchannels}")
    print()
    print("=" * 70)

    # Flick default (= R/G/B/A only via _resolve_channels)
    print("[A] Flick read_frame, channels=None (default RGB/A subset)")
    total, per, shape = bench_serial(paths, channels=None)
    print(f"    serial: total {total*1000:7.1f} ms  "
          f"({total*1000/len(paths):5.1f} ms/file)")
    print(f"    shape : {shape}  (channels kept)")
    print(f"    avg/frame: {sum(per)/len(per)*1000:.1f} ms  "
          f"max: {max(per)*1000:.1f} min: {min(per)*1000:.1f}")
    print()
    for w_ in (4, 8, 16):
        if w_ > args.workers:
            break
        total, per, _ = bench_parallel(paths, w_, channels=None)
        print(f"[A{w_}] Flick read_frame, parallel ({w_} workers)")
        print(f"      wall {total*1000:7.1f} ms  "
              f"effective fps {len(paths)/total:5.1f}")
        print(f"      per-frame avg {sum(per)/len(per)*1000:.1f} ms")

    print()
    print("=" * 70)

    # Explicit RGB only (3 channels) for comparison with pure-OIIO bench
    print("[B] Flick read_frame, channels=['R','G','B']")
    total, per, shape = bench_serial(paths, channels=["R", "G", "B"])
    print(f"    serial: total {total*1000:7.1f} ms  "
          f"({total*1000/len(paths):5.1f} ms/file)")
    print(f"    shape : {shape}")


if __name__ == "__main__":
    main()
