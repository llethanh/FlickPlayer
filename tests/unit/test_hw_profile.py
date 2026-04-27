"""Tests for ``img_player.perf.hardware`` — pure-logic HW heuristics.

Three groups:

* :func:`classify_gpu` — a table of real-world GL_RENDERER strings →
  expected ``GpuKind``. Includes a regression case for the
  Radeon-RX-vs-iGPU collision (both contain "radeon").
* :func:`compute_tune` — the three reference setups from the spec
  (laptop iGPU, laptop dGPU, workstation), plus extreme inputs that
  exercise the documented clamps.
* :func:`detect_hardware` — verifies the ``psutil`` and
  ``os.cpu_count`` fallbacks (we never want a missing dep to
  prevent the app from starting).
"""

from __future__ import annotations

import builtins

import pytest

from img_player.perf.hardware import (
    HardwareProfile,
    classify_gpu,
    compute_tune,
    detect_hardware,
)

# ============================================================================
# classify_gpu
# ============================================================================


@pytest.mark.parametrize(
    ("renderer", "expected_kind"),
    [
        # ---- Discrete NVIDIA — every consumer / pro variant ------
        ("NVIDIA GeForce RTX 5070 Laptop GPU/PCIe/SSE2", "discrete_nvidia"),
        ("NVIDIA GeForce RTX 3080", "discrete_nvidia"),
        ("Quadro RTX 5000/PCIe/SSE2", "discrete_nvidia"),
        ("Tesla T4/PCIe/SSE2", "discrete_nvidia"),
        # ---- Discrete AMD — RX / Pro / FirePro -------------------
        ("AMD Radeon RX 7900 XTX", "discrete_amd"),
        ("AMD Radeon Pro W6800", "discrete_amd"),
        ("AMD FirePro W7100", "discrete_amd"),
        # ---- Integrated AMD — the iGPU strings we'll meet --------
        ("AMD Radeon 780M Graphics", "integrated_amd"),
        ("AMD Radeon Vega 8 Graphics", "integrated_amd"),
        ("AMD Radeon Graphics", "integrated_amd"),
        # ---- Integrated Intel ------------------------------------
        ("Intel(R) UHD Graphics 770", "integrated_intel"),
        ("Intel(R) Iris(R) Xe Graphics", "integrated_intel"),
        ("Mesa Intel(R) Arc(tm) A770 Graphics", "integrated_intel"),
        ("Intel HD Graphics 630", "integrated_intel"),
        # ---- Unknown / empty → safe fallback ---------------------
        ("", "unknown"),
        (None, "unknown"),
        ("Some random string that is not a GPU", "unknown"),
    ],
)
def test_classify_gpu_table(renderer: str | None, expected_kind: str) -> None:
    assert classify_gpu(renderer) == expected_kind


def test_classify_gpu_radeon_rx_takes_precedence_over_generic_radeon() -> None:
    """Regression: a discrete Radeon RX must NOT be classified as
    ``integrated_amd`` just because both strings contain "radeon".

    The classifier checks discrete tokens first; this test pins that
    contract so a future re-ordering can't silently break it.
    """
    assert classify_gpu("AMD Radeon RX 7900 XTX") == "discrete_amd"
    assert classify_gpu("AMD Radeon Pro W6800") == "discrete_amd"


# ============================================================================
# compute_tune — reference setups from the spec
# ============================================================================

# These mirror the table in section 2 of the spec.
_LAPTOP_IGPU = HardwareProfile(
    cpu_threads=16,
    total_ram_gb=15.3,
    gpu_renderer="AMD Radeon 780M Graphics",
    gpu_kind="integrated_amd",
)
_LAPTOP_DGPU = HardwareProfile(
    cpu_threads=16,
    total_ram_gb=15.3,
    gpu_renderer="NVIDIA GeForce RTX 5070 Laptop GPU",
    gpu_kind="discrete_nvidia",
)
_WORKSTATION = HardwareProfile(
    cpu_threads=32,
    total_ram_gb=128.0,
    gpu_renderer="NVIDIA RTX A4000",
    gpu_kind="discrete_nvidia",
)


def test_tune_laptop_igpu_no_pbo_oiio_one() -> None:
    t = compute_tune(_LAPTOP_IGPU)
    assert t.num_workers == 8           # 16 // 2
    assert 6.0 <= t.cache_gb <= 6.2     # 15.3 * 0.4 ≈ 6.12
    assert t.oiio_threads == 1          # integrated → safe
    assert t.use_pbo is False


def test_tune_laptop_dgpu_keeps_oiio_at_one_due_to_consumer_ram() -> None:
    """On a 16 GB laptop with discrete GPU we still keep oiio=1.

    The GPU classification is ``discrete_nvidia`` so PBO is on, but
    the ``total_ram_gb < 32`` branch fires: a consumer-laptop
    DDR can't sustain multiple OIIO threads + a paint thread without
    saturating. Pinned by slice 4 bench C empirical regression of
    -32 % fps with oiio=4 on this exact profile.
    """
    t = compute_tune(_LAPTOP_DGPU)
    assert t.num_workers == 8
    assert 6.0 <= t.cache_gb <= 6.2
    assert t.oiio_threads == 1          # consumer RAM → keep tight
    assert t.use_pbo is True            # but PBO is still worth it


def test_tune_workstation_caps_workers_and_oiio() -> None:
    t = compute_tune(_WORKSTATION)
    assert t.num_workers == 12          # 32 // 2 = 16, capped to 12
    assert 51.0 <= t.cache_gb <= 51.5   # 128 * 0.4 = 51.2
    assert t.oiio_threads == 6          # 32 // 4 = 8, capped to 6
    assert t.use_pbo is True


# ============================================================================
# compute_tune — extreme inputs exercise the clamps
# ============================================================================


def test_tune_clamps_at_workers_min() -> None:
    """Even on a 2-thread machine we keep at least 2 workers.

    With only 4 GB total RAM this is squarely in the consumer-laptop
    branch, so ``oiio_threads`` stays at 1 (DDR-shared safety) — the
    dGPU floor of 2 only kicks in on workstations (≥ 32 GB).
    """
    hw = HardwareProfile(
        cpu_threads=2,
        total_ram_gb=4.0,
        gpu_renderer="NVIDIA GeForce GTX 1650",
        gpu_kind="discrete_nvidia",
    )
    t = compute_tune(hw)
    assert t.num_workers == 2
    assert t.oiio_threads == 1          # consumer RAM → keep tight


def test_tune_clamps_at_cache_floor() -> None:
    """Tiny RAM machines get the 2 GB cache floor — never below."""
    hw = HardwareProfile(
        cpu_threads=4,
        total_ram_gb=4.0,
        gpu_renderer="Intel UHD Graphics 630",
        gpu_kind="integrated_intel",
    )
    t = compute_tune(hw)
    assert t.cache_gb == 2.0


def test_tune_clamps_at_cache_ceiling() -> None:
    """Huge RAM workstations get the 64 GB cache ceiling."""
    hw = HardwareProfile(
        cpu_threads=128,
        total_ram_gb=512.0,
        gpu_renderer="NVIDIA RTX 6000 Ada Generation",
        gpu_kind="discrete_nvidia",
    )
    t = compute_tune(hw)
    assert t.cache_gb == 64.0
    assert t.num_workers == 12          # capped at the workers ceiling
    assert t.oiio_threads == 6          # capped at the OIIO ceiling


def test_tune_high_ram_laptop_with_dgpu_scales_oiio_threads() -> None:
    """A laptop with ≥ 32 GB RAM and discrete GPU is treated like a
    workstation for OIIO sizing — quad-channel DDR can sustain more
    threads. Pins the threshold side that the slice-4 bug isn't
    over-conservative.
    """
    hw = HardwareProfile(
        cpu_threads=16,
        total_ram_gb=32.0,
        gpu_renderer="NVIDIA GeForce RTX 4080 Laptop GPU",
        gpu_kind="discrete_nvidia",
    )
    t = compute_tune(hw)
    assert t.oiio_threads == 4          # 16 // 4, full scale path
    assert t.use_pbo is True


def test_tune_consumer_ram_threshold_boundary() -> None:
    """Fence-post: exactly *under* the threshold stays at 1 OIIO
    thread, exactly *at* the threshold scales up. Documents the
    boundary so a future tweak can't slip a regression past."""
    just_below = HardwareProfile(
        cpu_threads=16,
        total_ram_gb=31.9,
        gpu_renderer="discrete",
        gpu_kind="discrete_nvidia",
    )
    just_at = HardwareProfile(
        cpu_threads=16,
        total_ram_gb=32.0,
        gpu_renderer="discrete",
        gpu_kind="discrete_nvidia",
    )
    assert compute_tune(just_below).oiio_threads == 1
    assert compute_tune(just_at).oiio_threads == 4


def test_tune_unknown_gpu_is_safe() -> None:
    """Unknown classification must never enable PBO or bump OIIO threads.

    This is the load-bearing safety property: at boot time we call
    ``compute_tune`` with ``gpu_renderer=None`` (GL context not yet
    alive) which yields ``gpu_kind="unknown"``. The result is the
    same conservative tune as today's hard-coded defaults — so the
    boot path is provably non-regressing.
    """
    hw = HardwareProfile(
        cpu_threads=16,
        total_ram_gb=16.0,
        gpu_renderer=None,
        gpu_kind="unknown",
    )
    t = compute_tune(hw)
    assert t.use_pbo is False
    assert t.oiio_threads == 1


# ============================================================================
# detect_hardware — fallback paths
# ============================================================================


def test_detect_hardware_with_no_renderer_yields_unknown() -> None:
    """At boot, before the GL context exists, gpu_kind is unknown."""
    hw = detect_hardware(gpu_renderer=None)
    assert hw.gpu_kind == "unknown"
    assert hw.cpu_threads >= 1
    assert hw.total_ram_gb > 0


def test_detect_hardware_with_renderer_classifies() -> None:
    hw = detect_hardware(gpu_renderer="NVIDIA GeForce RTX 3080")
    assert hw.gpu_kind == "discrete_nvidia"
    assert hw.gpu_renderer == "NVIDIA GeForce RTX 3080"


def test_detect_hardware_falls_back_when_psutil_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``psutil`` raises on import, ``total_ram_gb`` is set to a
    non-zero pessimistic fallback rather than 0 / NaN.

    We never want a missing dep to prevent the app from booting.
    """
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psutil":
            raise ImportError("psutil unavailable in this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    hw = detect_hardware(gpu_renderer=None)
    assert hw.total_ram_gb > 0
    # The fallback is cpu_threads * 4 GB — should be at least 8 GB
    # on any modern machine running this test (>=2 cores).
    assert hw.total_ram_gb >= 8.0


def test_detect_hardware_falls_back_when_cpu_count_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``os.cpu_count()`` can return ``None`` in exotic sandboxes.
    The detector must not propagate a ``None`` into ``HardwareProfile``."""
    monkeypatch.setattr("os.cpu_count", lambda: None)

    hw = detect_hardware(gpu_renderer=None)
    # Spec mandates a sensible fallback (we use 8) — pin the value so
    # downstream code can rely on it.
    assert hw.cpu_threads == 8
