"""Tests for the network-source staging cache.

The staging cache copies network-source frames to a local SSD in a
background thread so image readers see local-fast I/O instead of
SMB latency. Pin the public contract:

* Files registered with the manager get copied to the staging root,
  preserving the original filename.
* ``staged_path_for`` returns ``None`` until the copy lands, then
  returns the local path.
* Already-staged files (from a previous session) get re-registered
  from disk without re-copying.
* Modified source files (size or mtime mismatch) trigger a re-copy.
* LRU eviction at the per-sequence level when total exceeds budget.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from img_player.cache.network_staging import (
    NetworkStagingManager,
    _file_matches,
    _sequence_hash,
)


@pytest.fixture
def staging_root(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    root.mkdir()
    return root


@pytest.fixture
def fake_network_dir(tmp_path: Path) -> Path:
    """Test stand-in for a network share. We can't bring a real SMB
    server up in CI, so we just use a tmp dir on the local FS — the
    manager doesn't actually require the source to be on a network
    filesystem (the ``is_network_path`` check happens in
    :meth:`register_sequence` and we test that separately)."""
    d = tmp_path / "fake_network"
    d.mkdir()
    return d


def _make_files(directory: Path, n: int, size_bytes: int = 1024) -> list[Path]:
    paths = []
    for i in range(n):
        p = directory / f"frame.{i:04d}.exr"
        p.write_bytes(b"x" * size_bytes)
        paths.append(p)
    return paths


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll ``predicate`` until True or timeout. Used to wait on the
    background copy worker without a hard sleep."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class TestSequenceHash:
    def test_stable_for_same_path(self, tmp_path: Path) -> None:
        h1 = _sequence_hash(tmp_path / "a")
        h2 = _sequence_hash(tmp_path / "a")
        assert h1 == h2

    def test_different_for_different_paths(self, tmp_path: Path) -> None:
        h1 = _sequence_hash(tmp_path / "a")
        h2 = _sequence_hash(tmp_path / "b")
        assert h1 != h2

    def test_hash_format_hex(self, tmp_path: Path) -> None:
        h = _sequence_hash(tmp_path / "anything")
        # 8-byte blake2b = 16 hex chars
        assert len(h) == 16
        int(h, 16)  # parses as hex


class TestFileMatches:
    def test_identical_files_match(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        target = tmp_path / "tgt"
        src.write_bytes(b"hello")
        target.write_bytes(b"hello")
        # Match mtime explicitly so we don't depend on FS clock
        ts = time.time()
        os.utime(str(src), (ts, ts))
        os.utime(str(target), (ts, ts))
        assert _file_matches(src, target) is True

    def test_size_diff_no_match(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        target = tmp_path / "tgt"
        src.write_bytes(b"hello world")
        target.write_bytes(b"hello")
        assert _file_matches(src, target) is False

    def test_old_target_no_match(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        target = tmp_path / "tgt"
        src.write_bytes(b"hello")
        target.write_bytes(b"hello")
        # Set target older than src by more than 2s
        now = time.time()
        os.utime(str(src), (now, now))
        os.utime(str(target), (now - 10.0, now - 10.0))
        assert _file_matches(src, target) is False


class TestStagingManager:
    @pytest.fixture(autouse=True)
    def _bypass_network_check(self, monkeypatch) -> None:
        """The manager refuses to stage paths that aren't on a network
        share. Tests use local tmp dirs as stand-ins; we monkeypatch
        ``is_network_path`` to return ``True`` so the registration
        codepath runs."""
        monkeypatch.setattr(
            "img_player.cache.network_staging.is_network_path",
            lambda _p: True,
        )

    def test_disabled_manager_is_no_op(
        self, staging_root: Path, fake_network_dir: Path,
    ) -> None:
        files = _make_files(fake_network_dir, 3)
        mgr = NetworkStagingManager(
            staging_root, max_total_gb=1.0, enabled=False,
        )
        mgr.start()
        n = mgr.register_sequence(fake_network_dir, files)
        assert n == 0
        assert mgr.staged_path_for(files[0]) is None
        mgr.shutdown()

    def test_files_get_copied(
        self, staging_root: Path, fake_network_dir: Path,
    ) -> None:
        files = _make_files(fake_network_dir, 3, size_bytes=512)
        mgr = NetworkStagingManager(staging_root, max_total_gb=1.0)
        mgr.start()
        try:
            queued = mgr.register_sequence(fake_network_dir, files)
            assert queued == 3
            # Wait for the background worker to finish all 3 copies
            assert _wait_for(
                lambda: all(
                    mgr.staged_path_for(p) is not None for p in files
                )
            ), "background copy did not complete in time"
            # Every staged path is a real file with the same name as
            # the original.
            for src in files:
                staged = mgr.staged_path_for(src)
                assert staged is not None
                assert staged.is_file()
                assert staged.name == src.name
                assert staged.read_bytes() == src.read_bytes()
        finally:
            mgr.shutdown()

    def test_returns_none_for_unregistered(
        self, staging_root: Path, tmp_path: Path,
    ) -> None:
        mgr = NetworkStagingManager(staging_root, max_total_gb=1.0)
        mgr.start()
        try:
            assert mgr.staged_path_for(tmp_path / "nope.exr") is None
        finally:
            mgr.shutdown()

    def test_reregister_from_disk_no_copy(
        self, staging_root: Path, fake_network_dir: Path,
    ) -> None:
        # First session: stage everything and shut down.
        files = _make_files(fake_network_dir, 2, size_bytes=512)
        mgr1 = NetworkStagingManager(staging_root, max_total_gb=1.0)
        mgr1.start()
        mgr1.register_sequence(fake_network_dir, files)
        _wait_for(
            lambda: all(mgr1.staged_path_for(p) is not None for p in files),
        )
        mgr1.shutdown()

        # Second session: register the same sequence — should NOT
        # re-queue any files (they're already on disk and match).
        mgr2 = NetworkStagingManager(staging_root, max_total_gb=1.0)
        mgr2.start()
        try:
            queued = mgr2.register_sequence(fake_network_dir, files)
            assert queued == 0, (
                "second-session registration shouldn't re-queue "
                "already-staged files"
            )
            # And all files report as staged immediately.
            for p in files:
                assert mgr2.staged_path_for(p) is not None
        finally:
            mgr2.shutdown()

    def test_modified_source_triggers_recopy(
        self, staging_root: Path, fake_network_dir: Path,
    ) -> None:
        files = _make_files(fake_network_dir, 1, size_bytes=512)
        mgr = NetworkStagingManager(staging_root, max_total_gb=1.0)
        mgr.start()
        try:
            mgr.register_sequence(fake_network_dir, files)
            _wait_for(lambda: mgr.staged_path_for(files[0]) is not None)
            mgr.shutdown()

            # Mutate the source: bigger content + newer mtime.
            files[0].write_bytes(b"a" * 4096)
            future = time.time() + 10.0
            os.utime(str(files[0]), (future, future))

            mgr2 = NetworkStagingManager(staging_root, max_total_gb=1.0)
            mgr2.start()
            try:
                queued = mgr2.register_sequence(fake_network_dir, files)
                assert queued == 1, (
                    "mtime-changed source must re-queue for copy"
                )
                _wait_for(lambda: mgr2.staged_path_for(files[0]) is not None)
                # New content present in the local copy.
                staged = mgr2.staged_path_for(files[0])
                assert staged is not None
                assert staged.read_bytes() == b"a" * 4096
            finally:
                mgr2.shutdown()
        finally:
            mgr.shutdown()

    def test_eviction_when_over_budget(
        self, staging_root: Path, tmp_path: Path,
    ) -> None:
        # Create TWO sequences in separate dirs, each 800 KB. Budget
        # 1 KB (~negligible) → after registering the second, the
        # first should be evicted whole.
        seq1_dir = tmp_path / "seq1"
        seq1_dir.mkdir()
        seq2_dir = tmp_path / "seq2"
        seq2_dir.mkdir()
        files1 = _make_files(seq1_dir, 4, size_bytes=200 * 1024)
        files2 = _make_files(seq2_dir, 4, size_bytes=200 * 1024)

        mgr = NetworkStagingManager(
            staging_root, max_total_gb=0.001,  # 1 MB budget
        )
        mgr.start()
        try:
            mgr.register_sequence(seq1_dir, files1)
            _wait_for(
                lambda: all(
                    mgr.staged_path_for(p) is not None for p in files1
                ),
            )
            # Now register seq2 — should trigger eviction of seq1.
            mgr.register_sequence(seq2_dir, files2)
            _wait_for(
                lambda: all(
                    mgr.staged_path_for(p) is not None for p in files2
                ),
            )
            # seq1 is gone from the map AND from disk.
            for p in files1:
                staged = mgr.staged_path_for(p)
                # Either the map dropped it OR the file is gone
                if staged is not None:
                    assert not staged.is_file()
        finally:
            mgr.shutdown()
