"""End-to-end bench of the staging cache on a real network sequence.

Phase 1: baseline — read_frame directly from the network share.
Phase 2: register the sequence with the staging manager, wait for
         the worker to copy everything to local, then re-read via
         read_frame (should now hit the local staging copy).

Output: per-frame and wall-clock for each phase. Phase 2 should be
~3x faster than Phase 1.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.cache.network_staging import NetworkStagingManager
from img_player.io.reader import (
    configure_oiio,
    read_frame,
    set_staging_lookup,
)


def time_read(path: Path) -> float:
    t0 = time.perf_counter()
    arr = read_frame(path, channels=None, as_half=True)
    return time.perf_counter() - t0


def parallel_read(paths, workers):
    times = []
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(time_read, p) for p in paths]
        for f in as_completed(futs):
            times.append(f.result())
    return time.perf_counter() - t0, times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--skip", type=int, default=80,
                    help="skip first N frames (cold)")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    configure_oiio(None)
    paths = sorted(Path(args.directory).glob("*.exr"))[args.skip:]
    if len(paths) < args.frames * 2:
        print(f"need {args.frames * 2} cold frames, got {len(paths)}")
        return

    # Two disjoint slices: baseline vs staged.
    n = args.frames
    baseline_paths = paths[:n]
    staged_paths = paths[n:2 * n]

    print(f"Baseline frames (direct M:\\): {[p.name for p in baseline_paths]}")
    print(f"Staged   frames (via local) : {[p.name for p in staged_paths]}")
    print()

    # --- Phase 1: baseline, no staging --------
    set_staging_lookup(None)
    print(f"Phase 1: BASELINE parallel {args.workers}w (direct M:\\)")
    wall1, times1 = parallel_read(baseline_paths, args.workers)
    avg1 = sum(times1) / len(times1) * 1000
    fps1 = len(baseline_paths) / wall1
    print(f"   wall   {wall1*1000:.0f} ms   per-frame avg {avg1:.0f} ms   "
          f"effective {fps1:.2f} fps")
    print()

    # --- Phase 2: staging active --------
    staging_root = Path(tempfile.mkdtemp(prefix="flick_e2e_staging_"))
    try:
        # Use a generous budget so eviction doesn't interfere.
        mgr = NetworkStagingManager(staging_root, max_total_gb=10.0)
        # Bypass the network-path check so this works on any FS.
        import img_player.cache.network_staging as ns_mod
        ns_mod.is_network_path = lambda _p: True  # type: ignore[assignment]
        mgr.start()
        try:
            print(f"Staging: copying {len(staged_paths)} frames "
                  f"to local SSD ({staging_root})...")
            seq_dir = staged_paths[0].parent
            mgr.register_sequence(seq_dir, staged_paths)
            # Wait until every file is staged
            t_stage_start = time.perf_counter()
            while True:
                if all(mgr.staged_path_for(p) is not None for p in staged_paths):
                    break
                if time.perf_counter() - t_stage_start > 60:
                    print("staging timed out")
                    return
                time.sleep(0.05)
            stage_dt = time.perf_counter() - t_stage_start
            print(f"   bulk copy of all {len(staged_paths)} frames: "
                  f"{stage_dt*1000:.0f} ms "
                  f"(= {stage_dt*1000/len(staged_paths):.0f} ms/file copied)")
            print()

            # Now read through the manager
            set_staging_lookup(mgr.staged_path_for)
            print(f"Phase 2: STAGED parallel {args.workers}w (local SSD)")
            wall2, times2 = parallel_read(staged_paths, args.workers)
            avg2 = sum(times2) / len(times2) * 1000
            fps2 = len(staged_paths) / wall2
            print(f"   wall   {wall2*1000:.0f} ms   per-frame avg {avg2:.0f} ms   "
                  f"effective {fps2:.2f} fps")
            print()

            # Verdict
            speedup_wall = wall1 / wall2 if wall2 > 0 else float("inf")
            speedup_per = avg1 / avg2 if avg2 > 0 else float("inf")
            print("─" * 60)
            print("RESULT")
            print(f"  Speedup wall-clock : {speedup_wall:.2f}x")
            print(f"  Speedup per-frame  : {speedup_per:.2f}x")
            if stage_dt < wall1:
                # Staging+staged-reads finished while baseline would
                # still be running — net win even on FIRST pass.
                print(f"  Net first-pass     : staging({stage_dt*1000:.0f}ms) + "
                      f"staged-reads({wall2*1000:.0f}ms) = "
                      f"{(stage_dt+wall2)*1000:.0f}ms "
                      f"vs baseline {wall1*1000:.0f}ms")
        finally:
            set_staging_lookup(None)
            mgr.shutdown()
    finally:
        shutil.rmtree(str(staging_root), ignore_errors=True)


if __name__ == "__main__":
    main()
