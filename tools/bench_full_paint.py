"""End-to-end paint bench: decode + GL upload + paint, measured.

Spawns a real GLViewport with PBO upload enabled (same as production
code path on the RTX 3080) and pushes 300 frames of the user's video
through ``set_frame`` at 60 fps wall-clock cadence. Records:

  * tick interval (= effective fire rate of our pseudo-timer)
  * upload_us reported by the PBO ring
  * paintGL total wall time

Run from C:\\dev\\FlickPlayer after a ``git pull``::

    conda activate img_player
    python tools/bench_full_paint.py

If paint p95 > 16.67 ms, the bottleneck is downstream of decode_at.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMainWindow

from img_player.media.video_renderer import VideoSourceManager  # noqa: E402
from img_player.render.gl_viewport import GLViewport  # noqa: E402

VIDEO = Path(
    r"C:\Users\lam.lethanh\Pictures\images\video\RAYMAN LEGENDS RETOLD - Trailer.mp4"
)
N_FRAMES = 300
FPS = 60.0


def main() -> None:
    print(f"=== {VIDEO.name} ===")
    print(f"  bench will run {N_FRAMES} ticks at {FPS} fps target\n")

    app = QApplication(sys.argv)
    win = QMainWindow()
    win.resize(1280, 720)
    gl = GLViewport(win)
    win.setCentralWidget(gl)
    win.show()

    # Force PBO on like the auto-tuned RTX 3080 path.
    gl.set_pbo_enabled(True)

    mgr = VideoSourceManager()
    # Prime the source — open decoder, kick prefetch.
    arr0 = mgr.decode_at("L1", VIDEO, 0.0)
    print(
        f"  primed: dtype={arr0.dtype} shape={arr0.shape}, prefetch warming...",
    )

    # Wait briefly for prefetch buffer.
    QApplication.processEvents()
    time.sleep(1.0)
    QApplication.processEvents()

    samples_tick = []
    samples_decode = []
    samples_paint = []
    samples_upload = []
    last_tick = None

    state = {"i": 0}

    def tick() -> None:
        now = time.perf_counter()
        nonlocal last_tick
        if last_tick is not None:
            samples_tick.append((now - last_tick) * 1000)
        last_tick = now

        if state["i"] >= N_FRAMES:
            timer.stop()
            app.quit()
            return

        t_decode_start = time.perf_counter()
        arr = mgr.decode_at("L1", VIDEO, state["i"] / FPS)
        decode_ms = (time.perf_counter() - t_decode_start) * 1000
        samples_decode.append(decode_ms)

        t_paint_start = time.perf_counter()
        gl.set_frame(arr)
        # Force the paint to actually execute (set_frame queues it).
        gl.repaint()
        paint_ms = (time.perf_counter() - t_paint_start) * 1000
        samples_paint.append(paint_ms)

        # GLViewport records the last upload us internally.
        upload_us = getattr(gl, "_last_upload_gpu_us", None)
        if upload_us is not None:
            samples_upload.append(upload_us / 1000.0)

        state["i"] += 1

    interval_ms = max(1, int(round(1000.0 / FPS)))
    print(f"  timer interval: {interval_ms} ms (Qt.PreciseTimer)\n")

    timer = QTimer()
    timer.setTimerType(Qt.PreciseTimer)
    timer.setInterval(interval_ms)
    timer.timeout.connect(tick)
    timer.start()

    app.exec()

    def stats(name: str, samples: list[float]) -> None:
        if not samples:
            print(f"  {name}: no samples")
            return
        samples = sorted(samples)
        p50 = samples[len(samples) // 2]
        p95 = samples[int(len(samples) * 0.95)]
        p99 = samples[int(len(samples) * 0.99)]
        avg = sum(samples) / len(samples)
        max_ = samples[-1]
        print(
            f"  {name:>14}: p50={p50:6.2f}  p95={p95:6.2f}  p99={p99:6.2f}  "
            f"avg={avg:6.2f}  max={max_:6.2f}  ms"
        )

    print()
    print("--- RESULTS ---")
    print(f"  60 fps budget = 16.67 ms/tick")
    stats("tick_interval", samples_tick)
    stats("decode_at", samples_decode)
    stats("paint_total", samples_paint)
    stats("gpu_upload", samples_upload)
    if samples_tick:
        eff = 1000.0 / (sum(samples_tick) / len(samples_tick))
        print(f"  effective fps = {eff:.1f}")

    mgr.shutdown()
    win.close()


if __name__ == "__main__":
    main()
