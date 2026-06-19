"""Does load_sequence prefetch from the playhead, or from the middle?

Builds the REAL PlayerController with a recording fake cache, calls
load_sequence on a 1001..1055 sequence (playhead lands on 1001), and
prints the order frames are requested + which frame got priority 0.

If frame 1001 is priority 0 -> order is correct (the user's "fills
from the middle" is decode-timing / residual RAM, not the planner).
If some middle frame is priority 0 -> the anchor is wrong.

Run: python tools/repro_prefetch_order.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from img_player.player.controller import PlayerController  # noqa: E402
from img_player.sequence.models import FrameInfo, SequenceInfo  # noqa: E402


class RecordingCache:
    """Duck-typed stand-in for MasterFrameCache that records the
    (frame, priority) of every request — no decode."""

    def __init__(self, first, last):
        self._first, self._last = first, last
        self.requests: list[tuple[int, int]] = []

    # --- methods load_sequence / prefetch touch ---
    def attach(self, sequence):  # noqa: ANN001
        pass

    def set_current_frame(self, f):  # noqa: ANN001
        self.current = f

    def set_direction(self, d):  # noqa: ANN001
        pass

    def set_loop_range(self, lo, hi, enabled):  # noqa: ANN001
        pass

    def master_range(self):
        return (self._first, self._last)

    def clear_pending(self):
        return 0

    def request(self, frame, priority=0):  # noqa: ANN001
        self.requests.append((frame, priority))
        return True

    def request_alt_channel(self, *a, **k):  # noqa: ANN002, ANN003
        return True

    def visible_alt_layer_ids(self):
        return []

    def alt_channel_groups_for_layer(self, *a, **k):  # noqa: ANN002, ANN003
        return []

    def is_gap_frame(self, f):  # noqa: ANN001
        return False

    def contains(self, f):  # noqa: ANN001
        return False

    def cached_frames(self):
        return frozenset()

    def missing_frames(self):
        return frozenset()


def _seq(first, last):
    frames = tuple(
        FrameInfo(path=Path(f"/fake/CHARS.{n}.exr"), frame_number=n)
        for n in range(first, last + 1)
    )
    return SequenceInfo(
        base_name="CHARS.", extension=".exr", directory=Path("/fake"),
        padding=4, frames=frames, width=1920, height=900,
    )


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841
    first, last = 1001, 1055
    cache = RecordingCache(first, last)
    ctrl = PlayerController(cache)
    ctrl.load_sequence(_seq(first, last))

    reqs = cache.requests
    print(f"playhead after load: {ctrl.state.current_frame}")
    print(f"total requests: {len(reqs)}")
    if not reqs:
        print("NO requests recorded")
        return
    by_prio = sorted(reqs, key=lambda x: x[1])
    print(f"lowest-priority (decoded FIRST): {by_prio[:8]}")
    p0_frame = by_prio[0][0]
    if p0_frame == first:
        print(f"  [OK] frame {first} (the playhead) has priority 0 — "
              f"prefetch anchors on the cursor.")
    else:
        print(f"  [BUG] frame {p0_frame} has priority 0, not the "
              f"playhead {first} — prefetch anchors on the wrong frame "
              f"(= 'fills from the middle').")
    ctrl.shutdown()


if __name__ == "__main__":
    main()
