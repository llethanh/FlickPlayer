"""Bench Flick's VideoSourceManager.decode_at on the real Rayman trailer.

Compare current vs post-fix wall time per frame to confirm the
swscale-rgba + simpler tail change actually gives the ~1.5x win the
component bench predicted.
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.media.video_renderer import VideoSourceManager


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--frames", type=int, default=30)
    args = ap.parse_args()

    p = Path(args.path)
    mgr = VideoSourceManager()
    layer_id = "test"

    # Warm up: prime the decoder, skip codec startup costs
    for i in range(5):
        mgr.decode_at(layer_id, p, i / 60.0)

    print(f"Profiling decode_at on {p.name} ({args.frames} frames)")
    print()

    # Forward sequential decode (= playback)
    times = []
    for i in range(args.frames):
        t = 5.0 + i / 60.0  # 5s offset to avoid codec warmup blocks
        t0 = time.perf_counter()
        arr = mgr.decode_at(layer_id, p, t)
        times.append(time.perf_counter() - t0)
    avg_play = sum(times)/len(times)*1000
    print(f"[playback forward]  avg {avg_play:5.1f} ms/frame   "
          f"max {max(times)*1000:5.1f}  min {min(times)*1000:5.1f}")
    print(f"   shape: {arr.shape}, dtype: {arr.dtype}")
    fps = 1000.0 / avg_play
    print(f"   theoretical max: {fps:5.1f} fps")
    print()

    # Random scrub WITHOUT fast_seek (precise, costly on AV1 long-GOP)
    import random
    random.seed(42)
    times = []
    duration = 120
    for _ in range(args.frames):
        t = random.uniform(0.0, duration)
        t0 = time.perf_counter()
        mgr.decode_at(layer_id, p, t)
        times.append(time.perf_counter() - t0)
    avg_scrub = sum(times)/len(times)*1000
    print(f"[scrub precise]     avg {avg_scrub:5.1f} ms/frame   "
          f"max {max(times)*1000:5.1f}  min {min(times)*1000:5.1f}")

    # Random scrub WITH fast_seek (= what happens during real drag)
    mgr.set_fast_seek_all(True)
    random.seed(42)
    times = []
    for _ in range(args.frames):
        t = random.uniform(0.0, duration)
        t0 = time.perf_counter()
        mgr.decode_at(layer_id, p, t)
        times.append(time.perf_counter() - t0)
    avg_fast = sum(times)/len(times)*1000
    print(f"[scrub fast_seek]   avg {avg_fast:5.1f} ms/frame   "
          f"max {max(times)*1000:5.1f}  min {min(times)*1000:5.1f}")
    print(f"   speedup vs precise: {avg_scrub/avg_fast:.1f}x")

    mgr.shutdown()


if __name__ == "__main__":
    main()
