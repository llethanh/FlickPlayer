"""Hardware-adaptive performance heuristics — pure logic.

Given a description of the running machine (`HardwareProfile`),
return the recommended tuning (`PerformanceTune`) used to dimension
the frame cache, the OIIO thread pool, the worker count, and the
upload strategy.

This module is **pure logic** — no Qt, no OIIO, no OpenGL. Inputs
are values, outputs are values, and unit tests can exercise every
branch without spinning up a window or a GL context.

See sections 1 and 2 of
`docs/specs/2026-04-26-hw-adaptive-perf-design.md` for the rationale
behind every heuristic, including why the bounds (12 workers max,
6 OIIO threads max, 64 GB cache max) are where they are. In short:
they come straight from the BASELINE.md bench results — they are
not arbitrary.

The expected lifetime is:

    >>> hw = detect_hardware(gpu_renderer=None)         # at boot
    >>> tune = compute_tune(hw)                         # heuristics
    >>> # ... CLI overrides applied (see slice 2) ...
    >>> # ... runtime constraints applied (see slice 3) ...
    >>> # later, once the GL context lives:
    >>> hw2 = detect_hardware(gpu_renderer=real_gl_renderer)
    >>> tune2 = compute_tune(hw2)                       # late-bind
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------------

GpuKind = Literal[
    "discrete_nvidia",
    "discrete_amd",
    "integrated_amd",
    "integrated_intel",
    "unknown",
]


@dataclass(frozen=True)
class HardwareProfile:
    """Snapshot of the running machine relevant for performance tuning.

    ``gpu_renderer`` may be ``None`` if the GL context isn't yet alive
    — at boot we call :func:`detect_hardware` with ``None``, and call
    it again after the viewport has initialised (late-bind tune).
    When ``gpu_renderer`` is ``None`` the resulting ``gpu_kind`` is
    ``"unknown"`` and the safe-default heuristics apply.
    """

    cpu_threads: int
    total_ram_gb: float
    gpu_renderer: str | None
    gpu_kind: GpuKind


@dataclass(frozen=True)
class PerformanceTune:
    """Resolved tuning the rest of the app consumes.

    Computed once by :func:`compute_tune` from a ``HardwareProfile``,
    then potentially adjusted by CLI overrides (slice 2),
    runtime memory constraints (slice 3), and the calibration
    profile (slice 6). Each adjustment returns a *new* instance —
    the dataclass is frozen so the precedence order is enforced by
    the type system.
    """

    num_workers: int
    cache_gb: float
    oiio_threads: int
    use_pbo: bool


# ----------------------------------------------------------------------------
# Heuristic constants
# ----------------------------------------------------------------------------

# These come from `perf/BASELINE.md` and the spec — not arbitrary numbers.
# Bumping any of them should be backed by a fresh benchmark.

_WORKERS_MIN = 2            # never go below; the cache needs concurrency
_WORKERS_MAX = 12           # past 12, lock contention on the cache dominates

_OIIO_MIN_DGPU = 2          # on dGPU we always have headroom for at least 2
_OIIO_MAX = 6               # 16 saturated DRAM in BASELINE; 6 is the safe ceiling
_OIIO_INTEGRATED = 1        # iGPU shares DRAM with the worker pool — keep it tight

_CACHE_FRACTION = 0.4       # 40 % of total RAM, leaves headroom for Nuke/DaVinci
_CACHE_MIN_GB = 2.0
_CACHE_MAX_GB = 64.0


# ----------------------------------------------------------------------------
# GPU classification
# ----------------------------------------------------------------------------


def classify_gpu(renderer: str | None) -> GpuKind:
    """Classify a ``glGetString(GL_RENDERER)`` string into a ``GpuKind``.

    Order matters here: the more specific tokens (e.g. ``"radeon rx"``
    or ``"radeon pro"``) are checked before the generic ``"radeon"``,
    so a Radeon RX 7900 XTX isn't mistaken for an iGPU just because
    both strings contain the word "radeon". A regression test pins
    that specific case.

    Returns ``"unknown"`` for ``None``, the empty string, or any
    renderer we can't confidently classify — and ``"unknown"``
    triggers the *safe* heuristics (no PBO, single OIIO thread)
    which match today's pre-tune behaviour.
    """
    if not renderer:
        return "unknown"
    r = renderer.lower()

    # Discrete first — these tokens take precedence over generic
    # "radeon" which also appears in iGPU strings (e.g. "AMD Radeon
    # Graphics" on some Ryzen APUs).
    if any(tok in r for tok in ("geforce", "rtx", "quadro", "tesla")):
        return "discrete_nvidia"
    if any(tok in r for tok in ("radeon rx", "radeon pro", "firepro", "fire ")):
        return "discrete_amd"

    # Then integrated.
    if "intel" in r and any(tok in r for tok in ("hd graphics", "iris", "uhd", "arc")):
        return "integrated_intel"
    if "radeon" in r and any(tok in r for tok in ("780m", "880m", "vega", "graphics")):
        return "integrated_amd"

    return "unknown"


# ----------------------------------------------------------------------------
# Heuristics
# ----------------------------------------------------------------------------


def compute_tune(hw: HardwareProfile) -> PerformanceTune:
    """Apply the spec's heuristics to a ``HardwareProfile``.

    Pure function: deterministic on inputs, no side effects, no I/O.
    The exact formulas and bounds are documented in section 2 of the
    spec; this implementation is the canonical reference.
    """
    # Workers: half the CPU threads, capped to avoid lock contention
    # on the frame cache. Floor at 2 so even a 2-thread machine has
    # concurrency.
    num_workers = max(_WORKERS_MIN, min(hw.cpu_threads // 2, _WORKERS_MAX))

    # Cache: a fraction of total RAM, clamped to a sensible range.
    # 40 % leaves headroom for the OS and other VFX apps (Nuke,
    # DaVinci, Blender) the user might run alongside img_player.
    cache_gb = max(
        _CACHE_MIN_GB,
        min(hw.total_ram_gb * _CACHE_FRACTION, _CACHE_MAX_GB),
    )

    # OIIO threads: 1 on integrated (shared DRAM contention) or
    # unknown (safe default). Scale up on discrete GPUs where the
    # decode work is purely CPU and we have memory bandwidth to
    # spare.
    if hw.gpu_kind.startswith("integrated") or hw.gpu_kind == "unknown":
        oiio_threads = _OIIO_INTEGRATED
    else:
        oiio_threads = max(_OIIO_MIN_DGPU, min(hw.cpu_threads // 4, _OIIO_MAX))

    # PBO async upload: only on discrete GPU. On iGPU with unified
    # memory the PBO path adds a memcpy without buying any DMA
    # parallelism — measured slower in `perf/PBO_NOTES.md`. On
    # unknown GPUs we stay on the safe sync path.
    use_pbo = hw.gpu_kind.startswith("discrete")

    return PerformanceTune(
        num_workers=num_workers,
        cache_gb=cache_gb,
        oiio_threads=oiio_threads,
        use_pbo=use_pbo,
    )


# ----------------------------------------------------------------------------
# Detection (the only side-effecting function in the module)
# ----------------------------------------------------------------------------


_FALLBACK_CPU_THREADS = 8
_FALLBACK_GB_PER_THREAD = 4.0  # pessimistic assumption when psutil is unavailable


def detect_hardware(gpu_renderer: str | None = None) -> HardwareProfile:
    """Build a ``HardwareProfile`` by introspecting the running machine.

    ``gpu_renderer`` is passed in by the caller because
    ``glGetString(GL_RENDERER)`` is only known after the GL context
    is alive — at boot time we call this with ``None``, and call it
    again later once the viewport has emitted its renderer signal
    (late-bind tune flow, see ``app.py`` in slice 4).

    Falls back to safe values if ``psutil`` is missing or
    ``os.cpu_count`` misbehaves: better to over-tune slightly than
    refuse to start.
    """
    try:
        cpu_threads = os.cpu_count() or _FALLBACK_CPU_THREADS
    except Exception:  # pragma: no cover — os.cpu_count rarely raises
        logger.warning("os.cpu_count() failed, falling back to %d", _FALLBACK_CPU_THREADS)
        cpu_threads = _FALLBACK_CPU_THREADS

    try:
        import psutil

        total_ram_gb = psutil.virtual_memory().total / (1024**3)
    except Exception:
        # `psutil` not installed, or `virtual_memory()` raised on a
        # weird container / sandbox. Fall back to a pessimistic
        # estimate tied to CPU count — typically 4 GB per logical
        # thread for modern hardware.
        logger.warning(
            "psutil unavailable, falling back to cpu_threads*%.1f GB total RAM estimate",
            _FALLBACK_GB_PER_THREAD,
        )
        total_ram_gb = float(cpu_threads) * _FALLBACK_GB_PER_THREAD

    gpu_kind = classify_gpu(gpu_renderer)

    return HardwareProfile(
        cpu_threads=cpu_threads,
        total_ram_gb=total_ram_gb,
        gpu_renderer=gpu_renderer,
        gpu_kind=gpu_kind,
    )
