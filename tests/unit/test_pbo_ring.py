"""Tests for the PBO ring buffer in ``gl_viewport._PboRing``.

The ring is the only complex stateful piece in slice 4. It manages
three GL buffers + three fences in lockstep, and it has to be correct
across these branches:

* ring index advances modulo 3 across consecutive uploads;
* size change re-allocates each PBO once;
* a fence that's still pending at the next wrap-around is reported
  as ``upload_gpu_pending`` and kept alive (no leak, no spurious
  delete);
* a ``glMapBufferRange`` returning NULL bubbles up an exception that
  the caller can use to disable the PBO path for the session.

We can't run real OpenGL in unit tests (no display in CI, no
context). Instead we mock ``OpenGL.GL`` entirely and assert on the
sequence of calls made to it. This is fragile against incidental
changes (re-ordering an unrelated GL call breaks the test), but
that's the price of pinning the *contract* of an external API
without spinning up a window.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture
def gl_mock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace ``OpenGL.GL`` symbols used by ``_PboRing`` with a
    ``MagicMock`` whose return values can be configured per test."""
    import img_player.render.gl_viewport as mod

    fake = MagicMock()
    # The constants the ring inspects via ==. MagicMock returns a unique
    # sentinel object for each attribute access, which is fine: we only
    # need them to compare equal to themselves.
    fake.GL_PIXEL_UNPACK_BUFFER = "GL_PIXEL_UNPACK_BUFFER"
    fake.GL_STREAM_DRAW = "GL_STREAM_DRAW"
    fake.GL_TEXTURE_2D = "GL_TEXTURE_2D"
    fake.GL_SYNC_GPU_COMMANDS_COMPLETE = "GL_SYNC_GPU_COMMANDS_COMPLETE"
    fake.GL_ALREADY_SIGNALED = "ALREADY"
    fake.GL_CONDITION_SATISFIED = "SATISFIED"
    fake.GL_TIMEOUT_EXPIRED = "TIMEOUT"
    fake.GL_MAP_WRITE_BIT = 0x2
    fake.GL_MAP_INVALIDATE_BUFFER_BIT = 0x8
    fake.GL_MAP_UNSYNCHRONIZED_BIT = 0x20
    # Default: each glGenBuffers(3) returns three distinct ids.
    fake.glGenBuffers.return_value = [101, 102, 103]
    # Default: glMapBufferRange returns a fake non-zero pointer (a real
    # mapped pointer would be a memory address, but ctypes.memmove
    # accepts any int-castable, and we monkey-patch memmove to a no-op).
    fake.glMapBufferRange.return_value = 0xDEADBEEF
    # Default: every fence we place is signalled by the time we check.
    fake.glClientWaitSync.return_value = "ALREADY"
    fake.glFenceSync.side_effect = [f"fence-{i}" for i in range(100)]

    monkeypatch.setattr(mod, "GL", fake)
    # Stub out memmove so we don't actually copy 64 MB anywhere.
    monkeypatch.setattr(mod.ctypes, "memmove", lambda dst, src, n: None)
    return fake


def _frame(w: int = 64, h: int = 64) -> np.ndarray:
    """A small fake float16 RGBA frame — its actual content doesn't
    matter for these tests (memmove is stubbed)."""
    return np.zeros((h, w, 4), dtype=np.float16)


# ============================================================================
# Allocation
# ============================================================================


def test_ring_allocates_three_pbos_on_first_upload(gl_mock: MagicMock) -> None:
    """First upload triggers ``glGenBuffers`` once for the ring and
    ``glBufferData`` four times: three for the initial size allocation
    of each PBO, plus one orphan on the slot we actually upload to."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    ring.upload(
        _frame(),
        gl_format=gl_mock.GL_RGBA,
        gl_type=gl_mock.GL_HALF_FLOAT,
        width=64,
        height=64,
    )

    assert gl_mock.glGenBuffers.call_count == 1
    # 3 (whole-ring allocation) + 1 (slot orphan during upload) = 4.
    assert gl_mock.glBufferData.call_count == 4


def test_ring_reallocates_when_size_grows(gl_mock: MagicMock) -> None:
    """A frame larger than the previous allocation re-buffers all
    three PBOs at the new size."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    ring.upload(_frame(64, 64), gl_format=0, gl_type=0, width=64, height=64)
    initial_calls = gl_mock.glBufferData.call_count

    # Reset the fence side_effect so the next uploads still get fresh
    # fence handles.
    ring.upload(_frame(128, 128), gl_format=0, gl_type=0, width=128, height=128)

    # The 128x128 upload triggers three ensure_allocated calls
    # (one per PBO) plus one orphan on the slot.
    assert gl_mock.glBufferData.call_count >= initial_calls + 4


def test_ring_does_not_reallocate_when_size_unchanged(gl_mock: MagicMock) -> None:
    """Second upload at the same size only re-orphans its slot, not
    all three PBOs."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)
    after_first = gl_mock.glBufferData.call_count
    ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    # Only the per-upload orphan should fire (one extra call), not
    # three more whole-ring re-buffer.
    assert gl_mock.glBufferData.call_count == after_first + 1


# ============================================================================
# Index wrap
# ============================================================================


def test_ring_index_wraps_modulo_three(gl_mock: MagicMock) -> None:
    """The internal ring index must cycle 0 → 1 → 2 → 0 → 1 → ...

    We snapshot ``_idx`` *before* each upload, so the sequence we see
    is the slot that was chosen for that upload. The post-loop value
    is the slot that *will* be used next — we pin it so the wrap is
    deterministic across the whole session, not just the first cycle.
    """
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    slots_used = []
    for _ in range(5):
        slots_used.append(ring._idx)
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    assert slots_used == [0, 1, 2, 0, 1]
    assert ring._idx == 2  # next upload would land on slot 2


# ============================================================================
# Fence-based GPU timing
# ============================================================================


def test_first_three_uploads_have_no_gpu_us(gl_mock: MagicMock) -> None:
    """The fence on slot N is placed during upload N. It can only be
    *read* during upload N+3 (next wrap). So the first three uploads
    have no fence to read and return ``upload_gpu_us=None``."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    out = [
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)
        for _ in range(3)
    ]
    for cpu_us, gpu_us, pending in out:
        assert cpu_us > 0
        assert gpu_us is None
        assert pending is False


def test_fourth_upload_reads_first_fence_when_signalled(gl_mock: MagicMock) -> None:
    """At upload N=3 the ring wraps to slot 0, which has the fence
    from upload N=0. We mock it as already signalled, so we should
    get a non-None ``upload_gpu_us``."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    for _ in range(3):
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    # Configure the mock so the fence-status query returns SATISFIED.
    gl_mock.glClientWaitSync.return_value = "ALREADY"
    _, gpu_us, pending = ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    assert gpu_us is not None and gpu_us > 0
    assert pending is False
    # The previous fence on slot 0 must have been deleted.
    assert gl_mock.glDeleteSync.called


def test_pending_fence_keeps_state_and_reports_pending(gl_mock: MagicMock) -> None:
    """If the fence is still in flight at the next wrap, we report
    ``upload_gpu_pending=True``, *don't* delete the fence, and don't
    return a timing value. The next wrap will try again."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    for _ in range(3):
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    gl_mock.glClientWaitSync.return_value = "TIMEOUT"
    delete_count_before = gl_mock.glDeleteSync.call_count
    _, gpu_us, pending = ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    assert gpu_us is None
    assert pending is True
    # Fence not deleted (we'll retry next wrap).
    assert gl_mock.glDeleteSync.call_count == delete_count_before


# ============================================================================
# Failure handling
# ============================================================================


def test_map_returning_null_raises_runtime_error(gl_mock: MagicMock) -> None:
    """A failing ``glMapBufferRange`` must propagate to the caller so
    the viewport can flip to the sync path. We also unbind the PBO
    target before raising — leaving it bound would corrupt subsequent
    sync-path uploads."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    gl_mock.glMapBufferRange.return_value = 0  # "NULL"

    with pytest.raises(RuntimeError, match="NULL"):
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    # We must unbind before raising (last call to glBindBuffer with id=0).
    last_pbo_bind = next(
        (c for c in reversed(gl_mock.glBindBuffer.call_args_list)
         if c.args[0] == "GL_PIXEL_UNPACK_BUFFER"),
        None,
    )
    assert last_pbo_bind is not None
    assert last_pbo_bind.args[1] == 0


def test_cleanup_releases_fences_and_buffers(gl_mock: MagicMock) -> None:
    """``cleanup()`` must call ``glDeleteSync`` for every live fence
    and ``glDeleteBuffers`` exactly once for the three PBOs.
    Idempotent: calling twice doesn't double-delete."""
    from img_player.render.gl_viewport import _PboRing

    ring = _PboRing()
    for _ in range(3):
        ring.upload(_frame(), gl_format=0, gl_type=0, width=64, height=64)

    delete_sync_before = gl_mock.glDeleteSync.call_count
    ring.cleanup()
    # Three fences live → three glDeleteSync calls during cleanup.
    assert gl_mock.glDeleteSync.call_count - delete_sync_before == 3
    assert gl_mock.glDeleteBuffers.called

    # Idempotency: a second cleanup is a no-op (no extra deletes).
    deletions_after_first = gl_mock.glDeleteBuffers.call_count
    ring.cleanup()
    assert gl_mock.glDeleteBuffers.call_count == deletions_after_first
