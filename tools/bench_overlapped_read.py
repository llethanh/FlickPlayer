"""Test if multi-threaded chunk reads beat single-sync read on SMB.

Mirrors what OpenRV does in FileStream::ASyncNonBuffering, but uses
Python threads instead of Win32 OVERLAPPED. Each thread opens its
own file handle and reads its slice — multiple read() syscalls in
flight = SMB can serve them concurrently (especially with SMB3
MultiChannel which supports parallel TCP streams).

If this beats the single-read baseline meaningfully, we wire it
into read_frame as a pre-step (bulk to RAM, then feed PyOpenEXR
via the bytes API — well, if PyOpenEXR's File() accepts bytes,
which it currently doesn't, so we'll need to write to a memfile).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def single_sync_read(path: str) -> tuple[float, int]:
    """The baseline — what Flick's read_frame ends up doing via OIIO
    or what we'd do as a pre-step. Single open + one big read."""
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        data = f.read()
    return time.perf_counter() - t0, len(data)


def multi_thread_chunked_read(
    path: str, num_workers: int, chunk_size: int,
) -> tuple[float, int]:
    """N threads, each with its own file handle, reads its chunk.

    This is the Pythonic equivalent of OpenRV's overlapped I/O. The
    GIL is released during each ``read()`` syscall, so N reads can
    truly proceed concurrently against the SMB share.
    """
    t0 = time.perf_counter()
    size = os.path.getsize(path)
    # Divide into chunks
    offsets = list(range(0, size, chunk_size))
    chunks: list[bytes | None] = [None] * len(offsets)

    def read_chunk(idx: int) -> tuple[int, bytes]:
        offset = offsets[idx]
        length = min(chunk_size, size - offset)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        return idx, data

    with ThreadPoolExecutor(max_workers=num_workers) as ex:
        futs = [ex.submit(read_chunk, i) for i in range(len(offsets))]
        for fut in as_completed(futs):
            idx, data = fut.result()
            chunks[idx] = data

    total = b"".join(chunks)  # type: ignore[arg-type]
    return time.perf_counter() - t0, len(total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--skip", type=int, default=70,
                    help="skip first N frames (avoid warm cache)")
    ap.add_argument("--frames", type=int, default=3,
                    help="how many cold frames to test each config")
    args = ap.parse_args()

    paths = sorted(Path(args.directory).glob("*.exr"))[args.skip:]
    if len(paths) < args.frames * 7:
        print(f"Only {len(paths)} cold frames available, need more")
        return
    paths = [str(p) for p in paths]
    print(f"Testing on {args.frames} cold frames per config")
    print()

    n = args.frames
    chunk_size_mb = 32

    # Baseline: single sync read
    configs: list[tuple[str, list[str], object]] = [
        ("single sync read       ", paths[0*n:1*n], ("single",)),
    ]
    # Multi-thread with different worker counts
    for i, w in enumerate((2, 4, 8, 16)):
        configs.append(
            (f"chunked {w:2d}w x{chunk_size_mb}MB     ",
             paths[(i+1)*n:(i+2)*n], ("multi", w)),
        )
    # Plus a single 32MB chunk variant (no multi-threading, but small chunk)
    configs.append(
        ("single sync (warm hot) ", paths[0*n:1*n], ("single",)),
    )

    print(f"{'Config':<28}  {'wall ms':>9}  {'MB/s':>7}  {'note':<30}")
    print("-" * 80)
    for label, slice_paths, cfg in configs:
        if not slice_paths or len(slice_paths) < n:
            print(f"{label} (not enough frames)")
            continue
        wall_total = 0.0
        bytes_total = 0
        for p in slice_paths:
            if cfg[0] == "single":
                dt, n_bytes = single_sync_read(p)
            else:
                dt, n_bytes = multi_thread_chunked_read(
                    p, cfg[1], chunk_size_mb * 1024 * 1024,
                )
            wall_total += dt
            bytes_total += n_bytes
        avg_dt = wall_total / len(slice_paths)
        mbps = (bytes_total / len(slice_paths)) / (1024 * 1024) / avg_dt
        print(f"{label}  {avg_dt*1000:>9.0f}  {mbps:>6.0f}  "
              f"avg over {len(slice_paths)} cold frames")


if __name__ == "__main__":
    main()
