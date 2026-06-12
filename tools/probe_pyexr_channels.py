"""Probe PyOpenEXR's actual channel key structure on this EXR."""

from __future__ import annotations
import sys
import time
import OpenEXR


def main() -> None:
    path = sys.argv[1]
    t0 = time.perf_counter()
    with OpenEXR.File(path) as f:
        dt_open = time.perf_counter() - t0
        print(f"Open + full read: {dt_open*1000:.1f} ms")
        # parts is a list of Part objects in 3.x
        parts = f.parts
        print(f"Number of parts: {len(parts)}")
        for i, part in enumerate(parts[:1]):
            print(f"\n=== Part {i} ===")
            # In PyOpenEXR 3.x, the channels attr is a dict of {name: Channel}
            chans = part.channels
            print(f"  channels dict type: {type(chans).__name__}")
            print(f"  Number of channels: {len(chans)}")
            keys = list(chans.keys())
            print(f"  First 8 keys: {keys[:8]}")
            print(f"  Last 4 keys: {keys[-4:]}")
            # Sample a channel
            if "R" in chans:
                ch = chans["R"]
                print(f"  'R' channel pixels: type={type(ch.pixels).__name__}, "
                      f"shape={ch.pixels.shape}, dtype={ch.pixels.dtype}")


if __name__ == "__main__":
    main()
