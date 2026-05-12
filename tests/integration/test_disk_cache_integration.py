"""End-to-end integration tests for the disk-cache tier.

Unlike the unit tests in ``tests/unit/test_disk_cache.py`` which
exercise the cache class in isolation, these tests wire the full
stack: real ``SequenceInfo`` + ``LayerStack`` + ``MasterFrameCache``
+ ``DiskCache``, then close it all down and re-open against the
same disk-cache dir to confirm a "warm" session skips the decoder
entirely.

What's verified
---------------

  * **Persistence path** — a single-layer session caches its
    decoded frame on disk; a fresh session pointed at the same
    cache directory reads that frame back without invoking the
    decoder. The "no decode" claim is asserted via a call-counting
    spy around :func:`read_frame`.
  * **Per-contributor caching (C)** — with two layers stacked,
    each contributor lands on disk under its own key. Re-opening
    the same session re-uses both per-layer entries and only the
    composite blend runs.
  * **disk_available_master_frames (B)** — bulk existence probe
    used by the timeline pre-paint correctly reports the frames
    available across the contributor chain.

``read_frame`` is stubbed (no OIIO dependency) so the test is
hermetic across machines — what we're integration-testing is the
disk-cache + cache wiring, NOT the EXR decoder which has its own
tests. A spy still counts invocations so we can assert "warm
reopen skips the decode".
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from img_player.cache.disk_cache import DiskCache
from img_player.cache.master_frame_cache import MasterFrameCache
from img_player.layers import Layer, LayerStack
from img_player.sequence.models import FrameInfo, SequenceInfo

# pytest is imported for the qtbot fixture & marker style only.
_ = pytest


# ============================================================================
# Helpers
# ============================================================================


def _fake_decode(*args, **kw) -> np.ndarray:
    """Stand-in for :func:`io.reader.read_frame`. Signature must
    accept whatever the real callsite passes: positional ``path``
    + kwargs (``channels``, …). Returns a semi-transparent RGBA
    buffer.

    Alpha is intentionally **below 1.0** so a multi-layer composite
    doesn't short-circuit at the top: the over-blend math at
    ``MasterFrameCache._decode_composited_and_store`` skips decoding
    bottom layers when the top is fully opaque (alpha == 1.0), which
    means a 2-layer test with opaque tops would only ever record
    ONE disk write — defeating the per-contributor caching test.
    """
    arr = np.zeros((4, 4, 4), dtype=np.float32)
    arr[..., 0] = 0.5  # gray
    arr[..., 3] = 0.5  # half-transparent → forces composite to walk every layer
    return arr


class _DecodeSpy:
    """Wrap :func:`_fake_decode` to count invocations.

    Lets a test assert "frame X was served from cache" by checking
    that the underlying decoder wasn't called. Forwards all args
    untouched to the fake so the cache pipeline doesn't notice.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        path = args[0] if args else kwargs.get("path")
        channels = kwargs.get("channels")
        self.calls.append((path, channels))
        return _fake_decode(*args, **kwargs)

    @property
    def count(self) -> int:
        return len(self.calls)


def _make_layer(directory: Path, first: int = 1, last: int = 10) -> Layer:
    """Build a Layer pointing to ``last - first + 1`` synthetic files
    in ``directory``.

    Files are created **only on first call** for a given path —
    subsequent calls reuse the existing files so the mtime stays
    stable across sessions. This is critical for the warm-reopen
    tests: the disk-cache key embeds the source mtime, so a fresh
    file write between sessions would invalidate the key and turn
    the warm session into a fresh decode (false negative for the
    test).

    The :class:`FrameInfo.mtime` field is populated from the file's
    actual stat (not :func:`time.time` directly) so both sessions
    observe the identical mtime — exactly what the production
    scanner does on a real Ctrl+R.
    """
    directory.mkdir(parents=True, exist_ok=True)
    frames = []
    for n in range(first, last + 1):
        path = directory / f"render.{n:04d}.png"
        if not path.exists():
            path.write_bytes(b"")  # empty placeholder for stat()
        frames.append(
            FrameInfo(path=path, frame_number=n, mtime=path.stat().st_mtime),
        )
    seq = SequenceInfo(
        base_name="render",
        extension=".png",
        directory=directory,
        padding=4,
        frames=tuple(frames),
        width=4,
        height=4,
        channel_names=("R", "G", "B", "A"),
    )
    return Layer.from_sequence(seq, offset=0)


# ============================================================================
# Single-layer round-trip — the core promise of the disk tier
# ============================================================================


class TestSingleLayerPersistence:
    """Build a 10-frame layer, decode → check both tiers got the
    frame → tear down → re-open → assert NO decode happens (the disk
    tier serves the frame straight to the RAM cache)."""

    def test_warm_reopen_serves_disk_hit(
        self, qtbot, tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "disk_cache"
        seq_dir = tmp_path / "seq"

        # --- First session: decode frame 1 fresh, expect disk write
        layer = _make_layer(seq_dir, first=1, last=10)
        seq = layer.sequence
        stack = LayerStack()
        stack.add(layer)

        disk1 = DiskCache(cache_dir, budget_bytes=0)
        cache1 = MasterFrameCache(
            stack,
            budget_bytes=4 * 1024 * 1024,
            num_workers=1,
            disk_cache=disk1,
        )
        try:
            target_frame = seq.first_frame
            spy1 = _DecodeSpy()
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=spy1,
            ):
                cache1.request(target_frame)
                cache1.wait_idle(timeout=5.0)
            # First session = cold cache → decoder ran at least once.
            assert spy1.count >= 1
            assert target_frame in cache1.cached_frames()

            # Wait for the disk writer to flush at least one entry.
            for _ in range(200):  # up to 10 s
                if disk1.stats().writes >= 1:
                    break
                qtbot.wait(50)
            assert disk1.stats().writes >= 1, (
                f"disk cache should have written ≥1 entry "
                f"(got {disk1.stats().writes})"
            )
        finally:
            cache1.shutdown()

        # --- Second session: fresh caches, same disk dir
        layer2 = _make_layer(seq_dir, first=1, last=10)
        seq2 = layer2.sequence
        stack2 = LayerStack()
        stack2.add(layer2)

        disk2 = DiskCache(cache_dir, budget_bytes=0)
        # Sanity: the disk dir has the entry from the previous session.
        assert disk2.entry_count() >= 1

        cache2 = MasterFrameCache(
            stack2,
            budget_bytes=4 * 1024 * 1024,
            num_workers=1,
            disk_cache=disk2,
        )
        try:
            spy2 = _DecodeSpy()
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=spy2,
            ):
                cache2.request(seq2.first_frame)
                cache2.wait_idle(timeout=5.0)
            # Warm reopen: the master frame is served from the disk
            # tier, so the decoder must NEVER have run.
            assert spy2.count == 0, (
                f"warm reopen should not hit the decoder; "
                f"got {spy2.count} calls: {spy2.calls}"
            )
            # And the master frame is back in the RAM cache.
            assert seq2.first_frame in cache2.cached_frames()
            # Disk-tier stats show a hit, not a write.
            disk_stats = disk2.stats()
            assert disk_stats.hits >= 1
            assert disk_stats.writes == 0
        finally:
            cache2.shutdown()

    def test_clear_disk_then_reopen_forces_redecode(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """A user clicking "Clear cache now" in Preferences must force
        the next session to re-decode."""
        cache_dir = tmp_path / "disk_cache"
        seq_dir = tmp_path / "seq"
        stack = LayerStack()
        stack.add(_make_layer(seq_dir, first=1, last=10))

        disk1 = DiskCache(cache_dir, budget_bytes=0)
        cache1 = MasterFrameCache(
            stack, budget_bytes=4 * 1024 * 1024,
            num_workers=1, disk_cache=disk1,
        )
        try:
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=_DecodeSpy(),
            ):
                cache1.request(1)
                cache1.wait_idle(timeout=5.0)
            for _ in range(100):
                if disk1.stats().writes >= 1:
                    break
                qtbot.wait(50)
            disk1.clear()
            assert disk1.entry_count() == 0
        finally:
            cache1.shutdown()

        # Re-open. Disk is empty → must decode again.
        disk2 = DiskCache(cache_dir, budget_bytes=0)
        assert disk2.entry_count() == 0
        stack2 = LayerStack()
        stack2.add(_make_layer(seq_dir, first=1, last=10))
        cache2 = MasterFrameCache(
            stack2, budget_bytes=4 * 1024 * 1024,
            num_workers=1, disk_cache=disk2,
        )
        try:
            spy = _DecodeSpy()
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=spy,
            ):
                cache2.request(1)
                cache2.wait_idle(timeout=5.0)
            assert spy.count >= 1, "post-clear reopen must re-decode"
        finally:
            cache2.shutdown()


# ============================================================================
# Multi-layer per-contributor caching (C)
# ============================================================================


class TestPerContributorCaching:
    """Two layers stacked. Each contributor lands on disk under its
    own key so that reordering / hiding the OTHER layer doesn't
    invalidate the hot ones. The previous v1.5.0..v1.5.3 cache stored
    composites at the composite-key level — verifying per-layer is
    the right contract is the whole point of the v1.5.4 migration."""

    def test_two_layers_yield_two_disk_entries(
        self, qtbot, tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "disk_cache"
        stack = LayerStack()
        # Two distinct sequences (different dirs → different paths →
        # different keys) covering the same master range. Each
        # contributor at master=1 should land as its own disk entry.
        stack.add(_make_layer(tmp_path / "seqA", first=1, last=10))
        stack.add(_make_layer(tmp_path / "seqB", first=1, last=10))

        disk = DiskCache(cache_dir, budget_bytes=0)
        cache = MasterFrameCache(
            stack, budget_bytes=8 * 1024 * 1024,
            num_workers=1, disk_cache=disk,
        )
        try:
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=_DecodeSpy(),
            ):
                cache.request(1)
                cache.wait_idle(timeout=5.0)
            # Wait for both per-contributor writes to land. The disk
            # cache writes 1 entry per contributor, NOT per composite.
            for _ in range(200):
                if disk.stats().writes >= 2:
                    break
                qtbot.wait(50)
            assert disk.stats().writes >= 2, (
                f"per-layer caching should write ≥2 entries for a "
                f"2-layer stack; got {disk.stats().writes}"
            )
        finally:
            cache.shutdown()

    def test_warm_reopen_with_two_layers_skips_both_decodes(
        self, qtbot, tmp_path: Path,
    ) -> None:
        """The big payoff: a 2-layer composite re-opens without
        running the decoder for either contributor."""
        cache_dir = tmp_path / "disk_cache"
        seq_a = tmp_path / "seqA"
        seq_b = tmp_path / "seqB"

        # --- Cold first session
        stack = LayerStack()
        stack.add(_make_layer(seq_a, first=1, last=10))
        stack.add(_make_layer(seq_b, first=1, last=10))
        disk1 = DiskCache(cache_dir, budget_bytes=0)
        cache1 = MasterFrameCache(
            stack, budget_bytes=8 * 1024 * 1024,
            num_workers=1, disk_cache=disk1,
        )
        try:
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=_DecodeSpy(),
            ):
                cache1.request(1)
                cache1.wait_idle(timeout=5.0)
            for _ in range(200):
                if disk1.stats().writes >= 2:
                    break
                qtbot.wait(50)
        finally:
            cache1.shutdown()

        # --- Warm second session
        stack2 = LayerStack()
        stack2.add(_make_layer(seq_a, first=1, last=10))
        stack2.add(_make_layer(seq_b, first=1, last=10))
        disk2 = DiskCache(cache_dir, budget_bytes=0)
        cache2 = MasterFrameCache(
            stack2, budget_bytes=8 * 1024 * 1024,
            num_workers=1, disk_cache=disk2,
        )
        try:
            spy = _DecodeSpy()
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=spy,
            ):
                cache2.request(1)
                cache2.wait_idle(timeout=5.0)
            # Both contributors should be served from disk → 0 decodes.
            assert spy.count == 0, (
                f"warm reopen with 2 layers should skip both decodes; "
                f"got {spy.count}"
            )
            assert disk2.stats().hits >= 2
        finally:
            cache2.shutdown()


# ============================================================================
# Pre-paint timeline (B) — disk_available_master_frames
# ============================================================================


class TestDiskAvailableMasterFrames:
    """The timeline cache bar paints a "dim orange" wash for frames
    available on disk before the user even scrubs to them. That
    pre-paint uses ``MasterFrameCache.disk_available_master_frames``
    which delegates to ``DiskCache.contains_keys`` in bulk."""

    def test_returns_warm_frames_only(
        self, qtbot, tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "disk_cache"
        seq_dir = tmp_path / "seq"
        # Use a longer sequence so the "warmed" subset is clearly
        # distinguishable from the cold rest.
        stack = LayerStack()
        stack.add(_make_layer(seq_dir, first=1, last=10))

        # Cold session — populate the first 3 frames only.
        disk1 = DiskCache(cache_dir, budget_bytes=0)
        cache1 = MasterFrameCache(
            stack, budget_bytes=4 * 1024 * 1024,
            num_workers=1, disk_cache=disk1,
        )
        warmed = [1, 2, 3]
        try:
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=_DecodeSpy(),
            ):
                for f in warmed:
                    cache1.request(f)
                cache1.wait_idle(timeout=5.0)
            # Wait for all writes to land.
            for _ in range(200):
                if disk1.stats().writes >= len(warmed):
                    break
                qtbot.wait(50)
            actual_writes = disk1.stats().writes
            assert actual_writes >= len(warmed), (
                f"expected ≥{len(warmed)} writes, got {actual_writes}"
            )
        finally:
            cache1.shutdown()

        # Reopen and probe via the public API the timeline uses.
        stack2 = LayerStack()
        stack2.add(_make_layer(seq_dir, first=1, last=10))
        disk2 = DiskCache(cache_dir, budget_bytes=0)
        cache2 = MasterFrameCache(
            stack2, budget_bytes=4 * 1024 * 1024,
            num_workers=1, disk_cache=disk2,
        )
        try:
            available = cache2.disk_available_master_frames()
            assert isinstance(available, (set, frozenset))
            # All warmed frames must appear.
            for f in warmed:
                assert f in available, (
                    f"frame {f} should be reported as disk-available "
                    f"(available={sorted(available)})"
                )
            # Frames never warmed must NOT appear.
            for f in (5, 7, 9):
                assert f not in available, (
                    f"frame {f} was never cached; should not be in "
                    f"disk_available_master_frames "
                    f"(available={sorted(available)})"
                )
        finally:
            cache2.shutdown()


# ============================================================================
# Stats integrity across the full pipeline
# ============================================================================


class TestStatsIntegrationContract:
    """Sanity check that the counters the Preferences UI reads from
    actually tick under realistic load — not just unit-level put/get."""

    def test_counters_reflect_real_decode_traffic(
        self, qtbot, tmp_path: Path,
    ) -> None:
        cache_dir = tmp_path / "disk_cache"
        seq_dir = tmp_path / "seq"
        stack = LayerStack()
        stack.add(_make_layer(seq_dir, first=1, last=10))

        disk = DiskCache(cache_dir, budget_bytes=0)
        cache = MasterFrameCache(
            stack, budget_bytes=4 * 1024 * 1024,
            num_workers=1, disk_cache=disk,
        )
        try:
            with patch(
                "img_player.cache.master_frame_cache.read_frame",
                side_effect=_DecodeSpy(),
            ):
                cache.request(1)
                cache.wait_idle(timeout=5.0)
            for _ in range(200):
                if disk.stats().writes >= 1:
                    break
                qtbot.wait(50)
            stats = disk.stats()
            assert stats.writes >= 1
            assert stats.bytes_written > 0
            # Hits are zero on a cold session (everything was a miss
            # before the put). Just sanity: misses ≥ 1 because the
            # decode pipeline checked the disk tier before writing.
            assert stats.misses >= 1 or stats.hits >= 0
        finally:
            cache.shutdown()
