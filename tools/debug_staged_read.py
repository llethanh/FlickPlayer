"""Isolate why Flick's read_frame on a staged-copy is slow."""

from __future__ import annotations
import shutil
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from img_player.io.reader import (
    configure_oiio,
    read_frame,
    set_staging_lookup,
    _PYOPENEXR_AVAILABLE,
    _staging_lookup,
)
import OpenEXR
import OpenImageIO as oiio


def main() -> None:
    configure_oiio(None)
    src_path = Path(r"M:\ayon_projects\RMLD\EPISODES\bubbleDreamerIntro\SEQ003\SH0030\work\Lighting\renders\maya\RMLD_SH0030_workfileLighting_v004\CHARS\CHARS.1090.exr")

    tmp = Path(tempfile.mkdtemp(prefix="debug_"))
    try:
        local = tmp / src_path.name
        print(f"Copying {src_path.name} ({src_path.stat().st_size / 1024**2:.0f} MB)")
        t0 = time.perf_counter()
        shutil.copyfile(str(src_path), str(local))
        copy_t = time.perf_counter() - t0
        print(f"  copy time : {copy_t*1000:.0f} ms")
        print()

        # Step A: PyOpenEXR direct on the LOCAL copy (cold cache?)
        t0 = time.perf_counter()
        with OpenEXR.File(str(local)) as f:
            chans = f.parts[0].channels
            import numpy as np
            arr = np.asarray(chans["RGBA"].pixels, dtype=np.float16)
        a_t = time.perf_counter() - t0
        print(f"  A. PyOpenEXR local read     : {a_t*1000:.0f} ms  shape={arr.shape}")

        # Step B: OIIO direct on the local copy
        t0 = time.perf_counter()
        inp = oiio.ImageInput.open(str(local))
        pix = inp.read_image(0, 3, oiio.HALF)
        inp.close()
        b_t = time.perf_counter() - t0
        print(f"  B. OIIO local read RGB      : {b_t*1000:.0f} ms  shape={pix.shape}")

        # Step C: Flick's read_frame on the local path directly
        t0 = time.perf_counter()
        arr_c = read_frame(local, channels=None, as_half=True)
        c_t = time.perf_counter() - t0
        print(f"  C. read_frame(local)        : {c_t*1000:.0f} ms  shape={arr_c.shape}")

        # Step D: Flick's read_frame on the network path with staging hook
        set_staging_lookup(lambda p: local if str(p) == str(src_path) else None)
        t0 = time.perf_counter()
        arr_d = read_frame(src_path, channels=None, as_half=True)
        d_t = time.perf_counter() - t0
        print(f"  D. read_frame(network, staged): {d_t*1000:.0f} ms  shape={arr_d.shape}")

        # Step E: Same as D, second time (should be very fast due to OS cache)
        t0 = time.perf_counter()
        arr_e = read_frame(src_path, channels=None, as_half=True)
        e_t = time.perf_counter() - t0
        print(f"  E. read_frame(network, staged, 2nd): {e_t*1000:.0f} ms")

        set_staging_lookup(None)

        # Step F: read_frame directly on M:\
        t0 = time.perf_counter()
        arr_f = read_frame(src_path, channels=None, as_half=True)
        f_t = time.perf_counter() - t0
        print(f"  F. read_frame(network, direct): {f_t*1000:.0f} ms")

    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    main()
