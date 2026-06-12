"""Profile where time is spent in Flick's video decode pipeline.

Times each step separately so we can see exactly which one is the
bottleneck. Tests both the current path (rgb24 → manual rgba) and
the proposed path (rgba directly from swscale).
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

import av  # type: ignore[import-untyped]
import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--n-frames", type=int, default=30)
    args = ap.parse_args()

    container = av.open(args.path)
    v = container.streams.video[0]
    v.thread_type = "AUTO"

    # Discard the first 5 frames to skip codec warmup
    decoded = []
    for i, frame in enumerate(container.decode(v)):
        if i < 5:
            continue
        decoded.append(frame)
        if len(decoded) >= args.n_frames:
            break
    container.close()

    print(f"Profiling {len(decoded)} frames at {decoded[0].width}x{decoded[0].height}")
    print()

    # === Step A: to_ndarray(format='rgb24') (CURRENT) ===
    t = []
    for frame in decoded:
        t0 = time.perf_counter()
        _ = frame.to_ndarray(format="rgb24")
        t.append(time.perf_counter() - t0)
    print(f"[A] to_ndarray('rgb24')                  : "
          f"avg {sum(t)/len(t)*1000:.1f} ms  "
          f"max {max(t)*1000:.1f}  min {min(t)*1000:.1f}")

    # === Step B: to_ndarray(format='rgba') (PROPOSED) ===
    t = []
    for frame in decoded:
        t0 = time.perf_counter()
        _ = frame.to_ndarray(format="rgba")
        t.append(time.perf_counter() - t0)
    print(f"[B] to_ndarray('rgba')                   : "
          f"avg {sum(t)/len(t)*1000:.1f} ms  "
          f"max {max(t)*1000:.1f}  min {min(t)*1000:.1f}")

    # === Step C: current decode_at logic — rgb24 -> manual rgba ===
    # rgb_u8 -> float32 * (1/255) -> empty rgba -> [:,:,:3] = rgb -> [:,:,3]=1
    t = []
    for frame in decoded:
        rgb_u8 = frame.to_ndarray(format="rgb24")
        t0 = time.perf_counter()
        rgb = rgb_u8.astype(np.float32, copy=False) * (1.0 / 255.0)
        h, w, _ = rgb.shape
        rgba = np.empty((h, w, 4), dtype=np.float32)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 1.0
        t.append(time.perf_counter() - t0)
    print(f"[C] current decode_at tail (post-rgb24)  : "
          f"avg {sum(t)/len(t)*1000:.1f} ms  "
          f"max {max(t)*1000:.1f}  min {min(t)*1000:.1f}")

    # === Step D: proposed decode_at — rgba uint8 -> float32 ===
    t = []
    for frame in decoded:
        rgba_u8 = frame.to_ndarray(format="rgba")
        t0 = time.perf_counter()
        rgba = rgba_u8.astype(np.float32, copy=False) * (1.0 / 255.0)
        t.append(time.perf_counter() - t0)
    print(f"[D] proposed decode_at tail (post-rgba)  : "
          f"avg {sum(t)/len(t)*1000:.1f} ms  "
          f"max {max(t)*1000:.1f}  min {min(t)*1000:.1f}")

    # === Step E: full current pipeline (A + C) ===
    t = []
    for frame in decoded:
        t0 = time.perf_counter()
        rgb_u8 = frame.to_ndarray(format="rgb24")
        rgb = rgb_u8.astype(np.float32, copy=False) * (1.0 / 255.0)
        h, w, _ = rgb.shape
        rgba = np.empty((h, w, 4), dtype=np.float32)
        rgba[:, :, :3] = rgb
        rgba[:, :, 3] = 1.0
        t.append(time.perf_counter() - t0)
    print()
    print(f"[E] FULL CURRENT pipeline (A + C)        : "
          f"avg {sum(t)/len(t)*1000:.1f} ms")

    # === Step F: full proposed pipeline (B + D) ===
    t = []
    for frame in decoded:
        t0 = time.perf_counter()
        rgba_u8 = frame.to_ndarray(format="rgba")
        rgba = rgba_u8.astype(np.float32, copy=False) * (1.0 / 255.0)
        t.append(time.perf_counter() - t0)
    print(f"[F] FULL PROPOSED pipeline (B + D)       : "
          f"avg {sum(t)/len(t)*1000:.1f} ms")

    # === Step G: full RGBA uint8, no float32 conversion ===
    t = []
    for frame in decoded:
        t0 = time.perf_counter()
        rgba_u8 = frame.to_ndarray(format="rgba")
        t.append(time.perf_counter() - t0)
    print(f"[G] uint8 RGBA only, no float32 cast     : "
          f"avg {sum(t)/len(t)*1000:.1f} ms  "
          f"(would need viewport uint8 path)")


if __name__ == "__main__":
    main()
