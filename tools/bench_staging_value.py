"""Validate: does network → local-staging → decode beat network → decode
on a REPEATED scrub (second pass)?

Scenario A — direct: read EXR from M:\ via OIIO (Flick's current path).
Scenario B — staged: copy M:\ → C:\TEMP, then OIIO reads C:\TEMP\
            (simulates what staging would do once a frame is local).
Scenario C — re-read from local on second pass (= what playback loop sees).

For staging to be worth the dev effort, scenario C should be meaningfully
faster than A. Otherwise we just slow down first-pass with no benefit.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import OpenImageIO as oiio


def time_oiio_read(path: str) -> float:
    t0 = time.perf_counter()
    inp = oiio.ImageInput.open(path)
    pix = inp.read_image(0, 3, oiio.HALF)
    inp.close()
    return time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("network_directory")
    ap.add_argument("--local-dir", default=os.environ.get("TEMP", "C:/Temp") + r"\flick_staging_bench")
    ap.add_argument("--skip", type=int, default=80, help="skip warm cache files")
    args = ap.parse_args()

    paths = sorted(Path(args.network_directory).glob("*.exr"))[args.skip:]
    if len(paths) < 5:
        print("not enough cold frames")
        return

    local_root = Path(args.local_dir)
    local_root.mkdir(parents=True, exist_ok=True)

    print(f"local staging dir: {local_root}")
    print()
    print(f"{'Scenario':<48}  {'time ms':>9}  {'note':<30}")
    print("-" * 95)

    # --- A: direct from network, cold ---
    for i, p in enumerate(paths[:3]):
        dt = time_oiio_read(str(p))
        print(f"A. direct OIIO from M:\\ (cold) #{i:<2}            {dt*1000:>9.0f}  {p.name}")

    # --- B: copy network -> local, then read local ---
    for i, p in enumerate(paths[3:6]):
        local = local_root / p.name
        if local.exists():
            local.unlink()
        # Step 1: copy
        t0 = time.perf_counter()
        shutil.copyfile(str(p), str(local))
        copy_dt = time.perf_counter() - t0
        # Step 2: read local
        read_dt = time_oiio_read(str(local))
        total = copy_dt + read_dt
        print(f"B. copy then read local (1st pass) #{i:<2}         "
              f"{total*1000:>9.0f}  copy={copy_dt*1000:.0f} read={read_dt*1000:.0f}")

    # --- C: re-read same local files (= 2nd pass scenario) ---
    print()
    for i, p in enumerate(paths[3:6]):
        local = local_root / p.name
        if not local.exists():
            continue
        dt = time_oiio_read(str(local))
        print(f"C. re-read same local file (= 2nd pass) #{i:<2}    "
              f"{dt*1000:>9.0f}  (already staged)")

    # --- D: even on cold sequences staging-ahead would dispatch reads
    #         BEFORE the user scrubs to them. Time the local-only read on
    #         freshly-copied files (= file is local on SSD, OS page cache
    #         likely warm-ish from the copy). ---
    print()
    for i, p in enumerate(paths[6:9]):
        local = local_root / p.name
        if local.exists():
            local.unlink()
        shutil.copyfile(str(p), str(local))  # background-stage proxy
        # Some time elapses (user scrubs through other frames)
        time.sleep(0.5)
        dt = time_oiio_read(str(local))
        print(f"D. stage-ahead, read after 500ms gap #{i:<2}        "
              f"{dt*1000:>9.0f}")

    # Cleanup
    shutil.rmtree(str(local_root), ignore_errors=True)


if __name__ == "__main__":
    main()
