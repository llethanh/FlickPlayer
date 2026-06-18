"""Repro the 2026-06-18 'cache empties just behind the playhead'
report WITHOUT the GUI or the network PNG sequence.

Builds a single-layer MasterFrameCache, injects frames [0..1016]
cached under the live signature, sets the playhead at 1016, a budget
that forces ~half to be evicted, then prints which frames survive.

Correct behaviour: survivors are a contiguous block ending at the
playhead (far-behind frames 0..N evicted first).
Bug behaviour: survivors are split — far-left kept, a hole just
behind the playhead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np

from img_player.cache.master_frame_cache import MasterFrameCache
from img_player.layers import Layer, LayerStack
from img_player.sequence.models import FrameInfo, SequenceInfo


def _seq(first: int, last: int) -> SequenceInfo:
    frames = tuple(
        FrameInfo(path=Path(f"/fake/{n:06d}.png"), frame_number=n)
        for n in range(first, last + 1)
    )
    return SequenceInfo(
        base_name="x", extension=".png", directory=Path("/fake"),
        padding=6, frames=frames, width=64, height=64,
    )


def _contiguous_runs(nums: list[int]) -> list[tuple[int, int]]:
    """Collapse a sorted list into [start, end] runs for readable
    printing."""
    if not nums:
        return []
    runs = []
    lo = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((lo, prev))
        lo = prev = n
    runs.append((lo, prev))
    return runs


def simulate_playback() -> None:
    """Dynamic check: replay forward playback from frame 0, filling
    ahead of the playhead and evicting each step, in full-range LOOP.
    Dump the cache shape at playhead 1107 (the user's screenshot) and
    assert it's a single contiguous window with the start evicted."""
    first, last = 0, 2251
    target_playhead = 1107
    ahead = 64
    frame_bytes = 1024 * 1024
    arr = np.zeros(frame_bytes // 4, dtype=np.float32)
    budget_frames = 992

    stack = LayerStack()
    stack.add(Layer.from_sequence(_seq(first, last), offset=0))
    cache = MasterFrameCache(stack, num_workers=1)
    try:
        cache.set_loop_range(first, last, enabled=True)
        cache.set_direction(1)
        cache._budget = budget_frames * frame_bytes

        for p in range(0, target_playhead + 1):
            cache._current_frame = p
            # Prefetch fills the playhead..playhead+ahead window.
            for f in range(p, min(p + ahead, last) + 1):
                sig = cache._signature_at(f)
                key = (f, sig)
                if key not in cache._frames:
                    cache._frames[key] = arr
                    cache._bytes_used += frame_bytes
            cache._evict_if_over_budget()

        survivors = sorted(mf for mf, _sig in cache._frames.keys())
        runs = _contiguous_runs(survivors)
        print(f"SIM @ playhead={target_playhead}: "
              f"{len(survivors)} cached, runs={runs}")
        ok = (
            len(runs) == 1
            and runs[0][0] > first            # start evicted
            and runs[0][0] <= target_playhead <= runs[0][1]  # spans cur
        )
        if ok:
            print(f"  [OK] single contiguous window {runs[0]} around "
                  f"the playhead; timeline start ({first}) evicted.")
        else:
            print(f"  [BUG] expected one window spanning the playhead "
                  f"with the start evicted; got {runs}")
    finally:
        cache.shutdown()


def main() -> None:
    import os as _os
    if _os.environ.get("SIM") == "1":
        simulate_playback()
        return
    first, last = 0, 2251
    playhead = 1016
    cached_lo, cached_hi = 0, 1016  # 1017 frames cached

    stack = LayerStack()
    stack.add(Layer.from_sequence(_seq(first, last), offset=0))

    # 1 MB per frame so the byte math is easy to read.
    frame_bytes = 1024 * 1024
    arr_template = np.zeros(frame_bytes // 4, dtype=np.float32)  # 1 MB

    cache = MasterFrameCache(stack, num_workers=1)
    try:
        # Inject frames under the live signature (= tier 2, live).
        for mf in range(cached_lo, cached_hi + 1):
            sig = cache._signature_at(mf)
            cache._frames[(mf, sig)] = arr_template
        cache._bytes_used = len(cache._frames) * frame_bytes
        import os as _os
        stale = _os.environ.get("STALE_PLAYHEAD") == "1"
        cache._current_frame = 0 if stale else playhead
        cache._direction = 1
        # Reproduce the real session: full-sequence LOOP enabled.
        if _os.environ.get("LOOP") == "1":
            cache._loop_enabled = True
            cache._loop_lo = first
            cache._loop_hi = last
            print("(LOOP enabled, full range)")
        print(f"(_current_frame set to {cache._current_frame} "
              f"{'[STALE]' if stale else '[live]'})")

        n_cached = len(cache._frames)
        # Budget = room for 500 frames → must evict ~517.
        keep_target = 500
        cache._budget = keep_target * frame_bytes

        print(f"before: cached={n_cached} [{cached_lo}..{cached_hi}] "
              f"playhead={playhead} "
              f"used={cache._bytes_used/1024**2:.0f}MB "
              f"budget={cache._budget/1024**2:.0f}MB")

        cache._evict_if_over_budget()

        survivors = sorted(mf for mf, _sig in cache._frames.keys())
        runs = _contiguous_runs(survivors)
        print(f"after:  survivors={len(survivors)}")
        print(f"        runs={runs}")
        if len(runs) == 1:
            lo, hi = runs[0]
            print(f"  [OK] CONTIGUOUS block [{lo}..{hi}] ending at "
                  f"playhead — far-behind evicted first (correct).")
        else:
            print(f"  [BUG] {len(runs)} fragments — a hole formed. "
                  f"Eviction is NOT pure far-behind-first.")
            # Show where the hole(s) are relative to the playhead.
            for lo, hi in runs:
                rel = "behind" if hi < playhead else (
                    "ahead" if lo > playhead else "spans")
                print(f"     [{lo}..{hi}] ({rel})")
    finally:
        cache.shutdown()


if __name__ == "__main__":
    main()
