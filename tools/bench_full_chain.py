"""Test the FULL chain: VideoSourceManager → _ThreadedDecoder → VideoSource.

The actual user-facing path. Confirm prefetch is running and the
cache benefits playback / scrub via this chain.
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
    args = ap.parse_args()

    p = Path(args.path)
    mgr = VideoSourceManager()
    layer_id = "test"

    # Force the manager to open the decoder so prefetch starts
    print("Opening decoder (kicks off prefetch in background)...")
    t0 = time.perf_counter()
    dec = mgr.get_or_open(layer_id, p)
    print(f"  open took {(time.perf_counter()-t0)*1000:.0f} ms")

    # Watch cache fill
    print()
    print("Watching VideoSource cache fill over 1.5s...")
    print(f"{'time':>6}  {'frames':>7}  {'MB':>7}  {'contig_to':>10}")
    print("-" * 50)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.5:
        time.sleep(0.1)
        # Peek directly into the VideoSource cache
        stats = dec._source.cache_stats()  # noqa: SLF001
        elapsed = time.perf_counter() - t0
        print(f"{elapsed:>5.1f}s  {stats['frames']:>7d}  "
              f"{stats['bytes']/1024**2:>6.0f}  {stats['contiguous_to']:>10d}")
        if stats['frames'] == 0 and elapsed > 0.5:
            print("  (no frames cached — prefetch may not be running)")
            break

    # Now actually decode some frames via the user-facing path
    print()
    print("Decoding via mgr.decode_at (= user path):")
    fps = float(dec._source.fps)  # noqa: SLF001
    for f_idx in (5, 30, 80, 5, 30):  # scrub pattern
        t_target = f_idx / fps + 0.001
        t0 = time.perf_counter()
        mgr.decode_at(layer_id, p, t_target)
        dt = (time.perf_counter() - t0) * 1000
        print(f"  decode frame {f_idx:3d}: {dt:6.1f} ms")

    mgr.shutdown()


if __name__ == "__main__":
    main()
