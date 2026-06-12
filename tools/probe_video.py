"""Probe a video file to understand its encoding + access pattern.

Looks at codec, resolution, fps, bitrate, GOP structure, pixel format,
and times: header parse, single seek-to-keyframe, decode 10 frames
forward, scrub-jump to random points.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import av  # type: ignore[import-untyped]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    args = ap.parse_args()

    path = Path(args.path)
    print(f"=== {path.name} ===")
    print(f"  File size: {path.stat().st_size / 1024**2:.1f} MB")
    print()

    t0 = time.perf_counter()
    container = av.open(str(path))
    t_open = time.perf_counter() - t0
    print(f"[1] Container open      : {t_open*1000:.0f} ms")

    streams_v = container.streams.video
    streams_a = container.streams.audio
    print(f"    Video streams: {len(streams_v)}    Audio streams: {len(streams_a)}")
    if not streams_v:
        print("    No video stream!")
        return

    v = streams_v[0]
    cc = v.codec_context
    print()
    print("--- Video stream 0 ---")
    print(f"  codec        : {cc.name}    (long: {cc.codec.long_name})")
    print(f"  pixel format : {cc.pix_fmt}")
    print(f"  resolution   : {cc.width} x {cc.height}")
    print(f"  fps          : avg_rate={float(v.average_rate or 0):.3f}  "
          f"base={v.time_base}")
    print(f"  duration     : {float(v.duration * v.time_base):.2f} s")
    n_frames = v.frames if v.frames > 0 else int(
        float(v.duration * v.time_base) * float(v.average_rate or 24)
    )
    print(f"  total frames : {n_frames}")
    print(f"  bit_rate     : {cc.bit_rate / 1e6:.2f} Mbps" if cc.bit_rate else "")
    print(f"  has B-frames : {cc.has_b_frames}")
    print(f"  thread_type  : {cc.thread_type}")
    print(f"  thread_count : {cc.thread_count}")
    if cc.codec.long_name:
        print(f"  profile      : {getattr(cc, 'profile', None)}")
    print()

    # Color metadata
    cs = cc.color_primaries
    tr = cc.color_trc
    rng = cc.color_range
    print(f"  primaries: {cs}  transfer: {tr}  range: {rng}")
    print()

    # GOP analysis — read first 60 packets and look at keyframes
    print("--- Packet analysis (first 100) ---")
    keyframes = []
    types = {}
    iframe_intervals = []
    last_keyframe = None
    t0 = time.perf_counter()
    count = 0
    container.seek(0)
    for packet in container.demux(v):
        if packet.dts is None:
            continue
        count += 1
        if packet.is_keyframe:
            keyframes.append(packet.pts)
            if last_keyframe is not None:
                iframe_intervals.append(
                    float((packet.pts - last_keyframe) * v.time_base),
                )
            last_keyframe = packet.pts
        if count >= 100:
            break
    t_demux = time.perf_counter() - t0
    print(f"  100 packets demuxed in: {t_demux*1000:.0f} ms")
    print(f"  Keyframes in first 100: {len(keyframes)}")
    if iframe_intervals:
        print(f"  Keyframe interval (sec): "
              f"min={min(iframe_intervals):.2f}  "
              f"max={max(iframe_intervals):.2f}  "
              f"avg={sum(iframe_intervals)/len(iframe_intervals):.2f}")
        print(f"  GOP size (approx)      : ~{int(sum(iframe_intervals)/len(iframe_intervals) * float(v.average_rate or 24))} frames")

    # Single-frame decode after seek to beginning
    print()
    print("--- Decode timings ---")
    container.seek(0)
    t0 = time.perf_counter()
    frames_read = 0
    first_frame_t = None
    for frame in container.decode(v):
        if frames_read == 0:
            first_frame_t = time.perf_counter() - t0
            print(f"  first frame ready       : {first_frame_t*1000:.0f} ms")
        frames_read += 1
        if frames_read >= 10:
            break
    t_total = time.perf_counter() - t0
    print(f"  decode 10 frames forward: {t_total*1000:.0f} ms  "
          f"({t_total*1000/10:.1f} ms/frame)")

    # Seek to middle, then 10 more
    fps = float(v.average_rate or 24)
    target_pts = int(n_frames / 2 * fps)
    container.seek(target_pts, any_frame=False, backward=True, stream=v)
    t0 = time.perf_counter()
    frames_read = 0
    for frame in container.decode(v):
        if frames_read == 0:
            print(f"  seek+1st-decode (middle): {(time.perf_counter()-t0)*1000:.0f} ms")
        frames_read += 1
        if frames_read >= 5:
            break
    t_seek = time.perf_counter() - t0
    print(f"  seek + 5 frames         : {t_seek*1000:.0f} ms  "
          f"({t_seek*1000/5:.1f} ms/frame)")

    # Multiple random seeks (simulates scrub)
    import random
    random.seed(42)
    seek_pts = [random.randint(0, n_frames - 10) * int(1 / fps / float(v.time_base)) for _ in range(5)]
    print()
    print("--- Scrub simulation (5 random seeks) ---")
    for i, pts in enumerate(seek_pts):
        container.seek(pts, any_frame=False, backward=True, stream=v)
        t0 = time.perf_counter()
        for frame in container.decode(v):
            break
        t_one = time.perf_counter() - t0
        print(f"  seek #{i+1}: {t_one*1000:.0f} ms")

    container.close()


if __name__ == "__main__":
    main()
