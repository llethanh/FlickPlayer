"""Better E2E bench: serial + 2nd-pass + concurrent-stage scenarios.

Phase 1: serial reads, no staging (baseline)
Phase 2: stage all files THEN serial reads (= simulates 'staging
         finished before user scrubs')
Phase 3: serial reads on the SAME staged files (= 2nd-pass / loop)
Phase 4: concurrent stage + reads (= realistic playback)
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.cache.network_staging import NetworkStagingManager
from img_player.io.reader import configure_oiio, read_frame, set_staging_lookup


def time_read(path: Path) -> float:
    t0 = time.perf_counter()
    read_frame(path, channels=None, as_half=True)
    return time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--skip", type=int, default=70)
    ap.add_argument("--frames", type=int, default=5)
    args = ap.parse_args()

    configure_oiio(None)
    paths = sorted(Path(args.directory).glob("*.exr"))[args.skip:]
    if len(paths) < args.frames * 3:
        print("need more cold frames")
        return

    n = args.frames
    bench_paths = paths[:n]
    staged_paths = paths[n:2*n]

    print(f"Test files (bench A): {[p.name for p in bench_paths]}")
    print(f"Test files (bench B): {[p.name for p in staged_paths]}")
    print()

    # --- Phase 1: BASELINE serial reads from M: ---
    set_staging_lookup(None)
    print("[Phase 1] BASELINE serial reads (direct M:\\)")
    total = sum(time_read(p) for p in bench_paths)
    avg1 = total*1000/n
    print(f"   {n} files: total {total*1000:.0f} ms ({avg1:.0f} ms/file)")
    print()

    # --- Phases 2 & 3: stage all THEN read serial ---
    staging_root = Path(tempfile.mkdtemp(prefix="flick_e2e_"))
    try:
        # IMPORTANT: only treat the M:\ source path as network — local
        # staging copies must still be classified as local so the
        # dispatcher in read_frame routes them through OIIO (fast on
        # local SSD). A blanket ``return True`` monkeypatch would
        # poison the routing decision and make staged reads look as
        # slow as direct network reads.
        import img_player.cache.network_staging as ns_mod
        ns_mod.is_network_path = (
            lambda p: str(p).upper().startswith("M:")
        )

        mgr = NetworkStagingManager(staging_root, max_total_gb=10.0)
        mgr.start()
        try:
            t0 = time.perf_counter()
            seq_dir = staged_paths[0].parent
            mgr.register_sequence(seq_dir, staged_paths)
            while not all(mgr.staged_path_for(p) for p in staged_paths):
                time.sleep(0.05)
            stage_dt = time.perf_counter() - t0
            print(f"[Phase 2 prep] bulk staging {n} files: {stage_dt*1000:.0f} ms "
                  f"({stage_dt*1000/n:.0f} ms/file)")

            set_staging_lookup(mgr.staged_path_for)

            # First pass after staging
            print("[Phase 2] STAGED serial reads (1st time hitting local copy)")
            total2 = sum(time_read(p) for p in staged_paths)
            avg2 = total2*1000/n
            print(f"   {n} files: total {total2*1000:.0f} ms ({avg2:.0f} ms/file)")
            print()

            # Second pass on same files
            print("[Phase 3] STAGED serial reads (2nd pass, page cache warm)")
            total3 = sum(time_read(p) for p in staged_paths)
            avg3 = total3*1000/n
            print(f"   {n} files: total {total3*1000:.0f} ms ({avg3:.0f} ms/file)")
            print()

            # --- Phase 4: simulate playback — stage worker + reads in parallel ---
            # Fresh sequence to ensure cold
            phase4_paths = paths[2*n:3*n]
            print(f"[Phase 4 prep] fresh frames: {[p.name for p in phase4_paths]}")

            # Use the SAME manager but new sequence; we register
            # then immediately start reading without waiting.
            stage_done_at = [0.0]

            def _on_staged(_key, _target):  # noqa: ARG001
                stage_done_at[0] = time.perf_counter()

            mgr._on_staged = _on_staged  # noqa: SLF001
            t0 = time.perf_counter()
            mgr.register_sequence(phase4_paths[0].parent, phase4_paths)
            # Mimic 'user scrubs' — read frames sequentially. Some may
            # already be staged, some may not. read_frame uses staged
            # path when available, else falls back to network.
            timings = []
            for p in phase4_paths:
                dt = time_read(p)
                timings.append(dt)
            t_total4 = time.perf_counter() - t0
            print(f"[Phase 4] CONCURRENT staging + reads")
            print(f"   {n} files: total {t_total4*1000:.0f} ms "
                  f"(avg {t_total4*1000/n:.0f} ms/file)")
            print(f"   individual: {[int(t*1000) for t in timings]}")
            print()

            # === Verdict ===
            print("=" * 60)
            print("VERDICT")
            print(f"  Baseline serial (direct M:\\)         : {avg1:5.0f} ms/file")
            print(f"  Staged serial 1st-pass               : {avg2:5.0f} ms/file "
                  f"(plus {stage_dt*1000/n:.0f} ms/file stage cost)")
            print(f"  Staged serial 2nd-pass (page cache)  : {avg3:5.0f} ms/file")
            print(f"  Concurrent stage + reads            : {t_total4*1000/n:5.0f} ms/file")
            print()
            print(f"  Speedup (stage+1st-pass vs baseline): "
                  f"{avg1/(avg2 + stage_dt*1000/n):.2f}x")
            print(f"  Speedup (2nd-pass vs baseline)      : "
                  f"{avg1/avg3:.2f}x")
        finally:
            set_staging_lookup(None)
            mgr.shutdown()
    finally:
        shutil.rmtree(str(staging_root), ignore_errors=True)


if __name__ == "__main__":
    main()
