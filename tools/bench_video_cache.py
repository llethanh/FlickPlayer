"""Validate the RAM cache benefit: play forward then scrub back.

Phase 1: forward decode 30 frames (= populates cache)
Phase 2: jump back to frame 5 (cache hit)
Phase 3: forward through frames 5..25 (all cache hits)
Phase 4: jump to frame 50 (cold, fills cache further)
Phase 5: jump back to 30 (cache hit, since 30 was in phase 1)
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
    args = ap.parse_args()

    p = Path(args.path)
    src = VideoSource(p)
    fps = float(src.fps)

    def t(i: int) -> float:
        return i / fps + 0.001

    def measure(label: str, indices: list[int]) -> None:
        times = []
        for i in indices:
            t0 = time.perf_counter()
            src.frame_at_time(t(i))
            times.append((time.perf_counter() - t0) * 1000)
        avg = sum(times) / len(times)
        st = src.cache_stats()
        print(f"  {label:<28}  avg {avg:6.1f} ms   "
              f"max {max(times):6.1f}   "
              f"cache: {st['frames']:4d} frames "
              f"({st['bytes']/1024**2:6.1f} MB)")
        return times

    print(f"=== {p.name} ({src.width}x{src.height} @ {fps:.0f} fps) ===")
    print()
    print(f"  Budget: {src.cache_stats()['budget']/1024**3:.1f} GB")
    print()

    # Phase 1: forward 20 frames cold
    print("Phase 1: forward decode (cold, populates cache)")
    measure("frames 5..24", list(range(5, 25)))

    # Phase 2: jump back to frame 5
    print()
    print("Phase 2: jump back to frame 5 (= should be in cache)")
    measure("frame 5", [5])

    # Phase 3: forward through cached frames
    print()
    print("Phase 3: forward through 5..24 (= all cache hits)")
    measure("frames 5..24 re-play", list(range(5, 25)))

    # Phase 4: jump to far frame 100 (= cold)
    print()
    print("Phase 4: cold jump to frame 100 (fills cache through forward decode)")
    measure("frame 100", [100])

    # Phase 5: jump back to frame 30 (= cached from phase 1 + traversal during phase 4)
    print()
    print("Phase 5: jump back to frame 30")
    measure("frame 30", [30])

    src.close()


if __name__ == "__main__":
    main()
