"""Hardware detection and performance tuning.

This package contains the logic that decides how to dimension the
runtime (worker pool, frame cache, OIIO threads, PBO usage) on a
given machine.

The submodules form three layers:

* `hardware` — *pure logic*: detect the running machine and apply
  heuristics. No Qt, no OIIO, no GL imports. Trivially unit-testable.
* `runtime_state` (slice 3) — *pure logic*: snapshot live memory
  pressure and clamp the static tune accordingly.
* `runtime_monitor` (slice 5) — Qt-aware: 1 Hz watchdog that emits
  warnings and shrinks the cache under load.
* `calibration` (slice 6) — Qt + GL aware: first-launch self-bench
  that persists a per-machine profile.

See `docs/specs/2026-04-26-hw-adaptive-perf-design.md` for the full
design.
"""

from img_player.perf.hardware import (
    GpuKind,
    HardwareProfile,
    PerformanceTune,
    apply_cli_overrides,
    classify_gpu,
    compute_tune,
    detect_hardware,
    log_tune_resolution,
)

__all__ = [
    "GpuKind",
    "HardwareProfile",
    "PerformanceTune",
    "apply_cli_overrides",
    "classify_gpu",
    "compute_tune",
    "detect_hardware",
    "log_tune_resolution",
]
