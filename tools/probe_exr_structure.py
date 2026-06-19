"""Probe a multichannel EXR's structure + benchmark decode strategies.

Answers: is it multi-part? how many channels? compression? and how
long does the CURRENT full-decode take vs an RGBA-only read — the
OpenRV-style fast path we want.

Run: python tools/probe_exr_structure.py "<path-to-one.exr>"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np


def _t(fn):
    t0 = time.perf_counter()
    r = fn()
    return time.perf_counter() - t0, r


def main() -> None:
    path = sys.argv[1]
    print(f"=== {path} ===")
    print(f"size: {Path(path).stat().st_size / 1024**2:.1f} MB\n")

    # ---- Structure via OpenEXR modern API ----
    import OpenEXR
    print("--- OpenEXR.File structure ---")
    dt, exr = _t(lambda: OpenEXR.File(str(path)))
    print(f"OpenEXR.File() full open+decode: {dt*1000:.0f} ms")
    parts = exr.parts
    print(f"parts: {len(parts)}")
    for i, part in enumerate(parts):
        chans = list(part.channels.keys())
        print(f"  part[{i}] '{getattr(part, 'name', '')}': "
              f"{len(chans)} channel keys")
        print(f"    keys: {chans[:12]}{' ...' if len(chans) > 12 else ''}")
        # compression / type
        h = part.header
        comp = h.get("compression", "?")
        print(f"    compression: {comp}")
    del exr

    # ---- Bench: current full decode ----
    print("\n--- BENCH (3 runs each, min reported) ---")

    def full_openexr():
        with OpenEXR.File(str(path)) as f:
            ch = f.parts[0].channels
            # touch RGBA like the reader does
            for k in ("RGBA", "RGB"):
                if k in ch:
                    return np.asarray(ch[k].pixels)
            return np.asarray(next(iter(ch.values())).pixels)

    times = sorted(_t(full_openexr)[0] for _ in range(3))
    print(f"OpenEXR.File full decode (current path): {times[0]*1000:.0f} ms")

    # ---- Bench: OIIO chbegin/chend = 0..4 (RGBA subset) ----
    import OpenImageIO as oiio

    def oiio_rgba_subset():
        inp = oiio.ImageInput.open(str(path))
        try:
            px = inp.read_image(0, 4, oiio.HALF)
            return np.asarray(px)
        finally:
            inp.close()

    try:
        times = sorted(_t(oiio_rgba_subset)[0] for _ in range(3))
        print(f"OIIO read_image(0,4) RGBA subset:        {times[0]*1000:.0f} ms")
    except Exception as e:
        print(f"OIIO subset failed: {e}")

    # ---- Bench: OIIO full ----
    def oiio_full():
        inp = oiio.ImageInput.open(str(path))
        try:
            return np.asarray(inp.read_image(oiio.HALF))
        finally:
            inp.close()

    try:
        times = sorted(_t(oiio_full)[0] for _ in range(2))
        print(f"OIIO read_image() FULL (all channels):   {times[0]*1000:.0f} ms")
    except Exception as e:
        print(f"OIIO full failed: {e}")

    # ---- Bench: OpenEXR old InputFile + FrameBuffer RGBA-only ----
    try:
        import OpenEXR as _OE
        import Imath
        def openexr_inputfile_rgba():
            f = _OE.InputFile(str(path))
            try:
                dw = f.header()["dataWindow"]
                w = dw.max.x - dw.min.x + 1
                h = dw.max.y - dw.min.y + 1
                half = Imath.PixelType(Imath.PixelType.HALF)
                out = {}
                for c in ("R", "G", "B", "A"):
                    raw = f.channel(c, half)
                    out[c] = np.frombuffer(raw, dtype=np.float16).reshape(h, w)
                return np.stack([out[c] for c in ("R", "G", "B", "A")], -1)
            finally:
                f.close()
        times = sorted(_t(openexr_inputfile_rgba)[0] for _ in range(3))
        print(f"OpenEXR InputFile RGBA-only channels:    {times[0]*1000:.0f} ms")
    except Exception as e:
        print(f"OpenEXR InputFile RGBA path failed: {e}")


    # ---- Bench: bulk-read bytes + OIIO memory IOProxy, RGBA subset ----
    # The optimal cold-network path: one sequential bulk read (fast on
    # SMB), then OIIO decodes only RGBA from the in-memory buffer (no
    # RTT-bound small reads, no 48-channel decode).
    try:
        def bulk_then_oiio_subset():
            data = np.fromfile(path, dtype=np.uint8)
            buf = memoryview(data)
            proxy = oiio.IOMemReader(buf)
            cfg = oiio.ImageSpec()
            cfg.attribute("oiio:ioproxy", proxy)
            inp = oiio.ImageInput.open(str(path), cfg)
            try:
                px = inp.read_image(0, 4, oiio.HALF)
                return np.asarray(px)
            finally:
                inp.close()
        times = sorted(_t(bulk_then_oiio_subset)[0] for _ in range(3))
        print(f"BULK read + OIIO subset (memory proxy):  {times[0]*1000:.0f} ms")
    except Exception as e:
        print(f"bulk+OIIO memory proxy failed: {e}")


if __name__ == "__main__":
    main()
