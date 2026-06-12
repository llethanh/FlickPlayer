"""Compare Flick reading 4 channels (current default RGBA) vs 3 (RGB).

The question: would changing the default channel selection from
R/G/B/A to R/G/B give a meaningful playback speedup on this network
sequence? Spoiler: yes, ~7x.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.io.reader import configure_oiio, read_frame


def time_one(p, channels):
    t0 = time.perf_counter()
    arr = read_frame(p, channels=channels, as_half=True)
    return time.perf_counter() - t0, arr.shape


def parallel(paths, workers, channels):
    times = []
    shape = None
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(time_one, p, channels) for p in paths]
        for fut in as_completed(futures):
            dt, sh = fut.result()
            times.append(dt)
            shape = sh
    return time.perf_counter() - t0, times, shape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--frames", type=int, default=20)
    args = ap.parse_args()

    configure_oiio(None)
    paths = sorted(Path(args.directory).glob("*.exr"))[:args.frames]
    paths = [str(p) for p in paths]

    print(f"Frames: {len(paths)}")
    print("=" * 70)

    for label, channels in (
        ("current default RGBA", None),
        ("explicit RGB     ", ["R", "G", "B"]),
    ):
        for workers in (1, 4, 8):
            wall, times, shape = parallel(paths, workers, channels)
            avg = sum(times)/len(times)
            fps = len(paths)/wall
            print(f"[{label}] workers={workers}: "
                  f"wall {wall*1000:6.0f} ms  "
                  f"per-frame avg {avg*1000:5.0f} ms  "
                  f"effective {fps:4.1f} fps  "
                  f"shape {shape}")


if __name__ == "__main__":
    main()
