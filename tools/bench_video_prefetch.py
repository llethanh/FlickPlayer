"""Watch the cache fill in real-time after open — should match the
OpenRV blue-bar growing-quickly behaviour the user described.
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.media.video_source import VideoSource


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--seconds", type=float, default=4.0)
    args = ap.parse_args()

    p = Path(args.path)
    print(f"=== {p.name} ===")
    print()

    t0 = time.perf_counter()
    src = VideoSource(p)
    open_dt = time.perf_counter() - t0
    print(f"open + prefetch start: {open_dt*1000:.0f} ms")
    print()

    # Watch the cache fill for the next N seconds
    print(f"{'time':>6}  {'frames':>7}  {'contiguous_to':>14}  "
          f"{'MB':>6}  {'fill rate':>12}")
    print("-" * 60)
    last_t = time.perf_counter()
    last_bytes = 0
    interval = 0.1
    while time.perf_counter() - t0 < args.seconds:
        time.sleep(interval)
        stats = src.cache_stats()
        elapsed = time.perf_counter() - t0
        rate_mb = (stats["bytes"] - last_bytes) / 1024**2 / interval
        last_bytes = stats["bytes"]
        if stats["frames"] == 0:
            continue
        print(f"{elapsed:>5.1f}s  "
              f"{stats['frames']:>7d}  "
              f"{stats['contiguous_to']:>14d}  "
              f"{stats['bytes']/1024**2:>6.0f}  "
              f"{rate_mb:>9.0f} MB/s")
        if stats["bytes"] >= stats["budget"] * 0.95:
            print(f"  → budget hit, prefetch will stop")
            break

    print()
    # Now test: reading frame 50 should be instant if it's cached
    fps = float(src.fps)
    test_frames = [0, 10, 50, 100, 200, 500, 1000]
    print("Test reads after prefetch:")
    for f in test_frames:
        stats = src.cache_stats()
        in_cache = f <= stats["contiguous_to"]
        t1 = time.perf_counter()
        try:
            src.frame_at_time(f / fps + 0.001)
        except Exception:
            print(f"  frame {f:5d}: ERROR")
            continue
        dt = (time.perf_counter() - t1) * 1000
        tag = "CACHE HIT" if in_cache else "cold decode"
        print(f"  frame {f:5d}: {dt:5.1f} ms  ({tag})")

    src.close()


if __name__ == "__main__":
    main()
