"""Sounddevice-backed audio output with a feeder-thread ring buffer.

Owns one persistent ``OutputStream`` (open from first video-with-audio
layer through app shutdown — option (b) of the design discussion: no
play-time latency at the cost of holding the device). The PortAudio
callback runs on the audio thread; we never call PyAV from there.
A feeder thread on the Python side pulls samples from the active
:class:`AudioSource` and pushes them into a thread-safe queue the
callback drains.

Sync model: video is the master clock (option (b) of the design
discussion). The :class:`AudioOutput` exposes ``play()`` / ``pause()``
/ ``seek(t)`` / ``set_speed(speed)`` / ``set_source(layer_id, audio_source)``
that the app wires to the player controller's ``state_changed`` signal.
Speed != 1.0 mutes (no time-stretch) per the user's chosen policy.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Optional

import numpy as np

from img_player.media.audio_source import AudioSource

log = logging.getLogger(__name__)

# Default device format. 48 kHz stereo covers every consumer card +
# every video container in practice. We could query the device's
# preferred rate at open time, but the resample is happening in PyAV
# either way (source rate → 48 kHz) so picking a fixed output keeps
# the rest of the code simpler.
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 2

# Ring buffer target depth in seconds. ~500 ms is well above
# typical OS scheduler jitter on Windows; smaller values risk
# underruns on a stuttering laptop.
_RING_BUFFER_SECONDS = 0.5

# How many samples per feeder iteration. Smaller = lower seek
# latency, larger = less overhead. 1024 ≈ 21 ms at 48 kHz, a good
# sweet spot.
_FEED_BLOCK_SAMPLES = 1024


class AudioOutput:
    """Persistent sounddevice OutputStream with a feeder thread.

    Lifecycle:
      - ``open()`` creates the device + starts the feeder thread.
      - ``close()`` stops both.
      - ``set_source(layer_id, source)`` swaps the active AudioSource;
        ``None`` means "no audio, output silence". The previous source
        (if owned) is closed.
      - ``play()`` / ``pause()`` toggle whether the feeder reads from
        the source or writes silence.
      - ``seek(t)`` repositions the active source and flushes the
        ring buffer so the user hears the new position immediately.
      - ``set_speed(speed)`` mutes when speed != 1.0 (option 2(a)).
      - ``set_gain(g)`` global linear gain on the final mix.
    """

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._stream = None  # type: Optional[object]
        # Active source — the AudioSource the feeder reads from; None
        # → feed silence. Owned by this object: ``set_source`` closes
        # the previous one.
        self._source_lock = threading.Lock()
        self._active_layer_id: str | None = None
        self._active_source: AudioSource | None = None
        # Playback flags — read by the feeder + callback.
        self._is_playing = threading.Event()
        self._mute = False
        self._gain = 1.0
        # Ring buffer: ``queue.Queue`` of (N, channels) ndarrays.
        # Bounded so the feeder blocks rather than runs ahead by
        # arbitrary amounts after a long pause.
        max_chunks = int(
            _RING_BUFFER_SECONDS * sample_rate / _FEED_BLOCK_SAMPLES,
        ) + 4
        self._ring: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_chunks)
        # Feeder thread state.
        self._feeder_stop = threading.Event()
        self._feeder_thread: threading.Thread | None = None
        # Carry-over from a partial chunk consumed by the callback —
        # callback reads can split a feeder chunk; the unused tail is
        # stashed here for the next callback.
        self._cb_carry: np.ndarray = np.zeros(
            (0, channels), dtype=np.float32,
        )
        # Lazy import — sounddevice is a heavy dep with PortAudio init,
        # don't pay that cost at module import time.
        self._sd = None  # type: object | None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Open the device and start the feeder. Idempotent."""
        if self._stream is not None:
            return
        try:
            import sounddevice as sd
        except OSError as exc:
            log.warning("[audio] sounddevice unavailable: %s", exc)
            return
        self._sd = sd
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception:  # pragma: no cover — depends on host audio
            log.exception("[audio] failed to open output stream")
            self._stream = None
            return
        self._feeder_stop.clear()
        self._feeder_thread = threading.Thread(
            target=self._feeder_loop, name="audio-feeder", daemon=True,
        )
        self._feeder_thread.start()

    def close(self) -> None:
        """Stop the feeder + the stream. Closes any owned AudioSource."""
        self._feeder_stop.set()
        if self._feeder_thread is not None:
            self._feeder_thread.join(timeout=1.0)
            self._feeder_thread = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.exception("[audio] error closing stream")
            self._stream = None
        with self._source_lock:
            if self._active_source is not None:
                try:
                    self._active_source.close()
                except Exception:
                    pass
                self._active_source = None
                self._active_layer_id = None

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def set_source(self, layer_id: str | None, source: AudioSource | None) -> None:
        """Swap the active AudioSource. Closes the previous one.

        ``None`` for both args means "no audio", the feeder produces
        silence. Used when the topmost-visible video layer has no
        audio track, or no video layer is at the playhead.
        """
        with self._source_lock:
            if self._active_layer_id == layer_id and source is self._active_source:
                return
            if self._active_source is not None:
                try:
                    self._active_source.close()
                except Exception:
                    pass
            self._active_source = source
            self._active_layer_id = layer_id
        # Flush the ring buffer so the user hears the new source's
        # samples (not the previous one's tail) on the next callback.
        self._flush_ring()

    def play(self) -> None:
        """Start consuming samples from the source. Idempotent."""
        self._is_playing.set()

    def pause(self) -> None:
        """Stop reading from the source — output silence. Idempotent."""
        self._is_playing.clear()

    def seek(self, t_seconds: float) -> None:
        """Reposition the active source to ``t_seconds`` and flush the
        ring buffer so the next callback hears the new position."""
        with self._source_lock:
            if self._active_source is not None:
                try:
                    self._active_source.seek(max(0.0, t_seconds))
                except Exception:
                    log.exception("[audio] seek failed")
        self._flush_ring()

    def set_speed(self, speed: float) -> None:
        """Mute when ``speed`` != 1.0. Per option 2(a) of the design.

        We don't time-stretch (would need rubberband / soundtouch);
        playback at non-standard speed simply goes silent. Restoring
        speed=1.0 unmutes immediately on the next callback.
        """
        self._mute = abs(speed - 1.0) > 1e-3

    def set_gain(self, gain: float) -> None:
        """Set the global linear gain (0.0 = silent, 1.0 = unity)."""
        self._gain = max(0.0, float(gain))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flush_ring(self) -> None:
        """Empty the ring buffer + carry. Called on seek / source swap."""
        while not self._ring.empty():
            try:
                self._ring.get_nowait()
            except queue.Empty:
                break
        self._cb_carry = np.zeros((0, self._channels), dtype=np.float32)

    def _feeder_loop(self) -> None:
        """Pull from the active source, push to the ring buffer.

        Runs on its own Python thread (= GIL-bound but PyAV decode
        releases the GIL during the actual decode work, so audio
        decode runs in parallel with the main / GUI threads). Sleeps
        briefly when the ring is full or no source is active to
        avoid burning CPU.
        """
        while not self._feeder_stop.is_set():
            if not self._is_playing.is_set():
                # Paused — feed nothing, just sleep so the callback
                # outputs silence and the ring drains naturally.
                self._feeder_stop.wait(timeout=0.05)
                continue
            with self._source_lock:
                source = self._active_source
            if source is None:
                self._feeder_stop.wait(timeout=0.05)
                continue
            try:
                block = source.read(_FEED_BLOCK_SAMPLES)
            except Exception:
                log.exception("[audio] feeder read failed")
                self._feeder_stop.wait(timeout=0.05)
                continue
            if block.shape[0] == 0:
                # EOF on the active source — sleep a bit; the
                # controller will issue a seek when the user wraps /
                # restarts.
                self._feeder_stop.wait(timeout=0.05)
                continue
            try:
                # Block when the ring is full so we don't overrun.
                self._ring.put(block, timeout=0.5)
            except queue.Full:
                # Stop signalled while waiting — loop and re-check.
                continue

    def _callback(
        self, outdata: np.ndarray, frames: int, time_info, status,
    ) -> None:
        """PortAudio audio-thread callback. NEVER call PyAV here.

        Drains chunks from the ring buffer into ``outdata``. Fills
        the remainder with silence on underrun, applies the global
        gain + mute flags. ``time_info`` and ``status`` are unused
        for the v1 sync model (video = master); they'd matter for
        an audio-master clock later.
        """
        del time_info, status
        out = outdata.reshape(frames, self._channels)
        # Mute or paused → silence.
        if self._mute or not self._is_playing.is_set():
            out.fill(0.0)
            return
        written = 0
        # First, drain the carry-over from the previous callback.
        if self._cb_carry.shape[0] > 0:
            take = min(frames, self._cb_carry.shape[0])
            out[:take] = self._cb_carry[:take]
            self._cb_carry = self._cb_carry[take:]
            written += take
        # Then pull fresh chunks from the ring until we've filled
        # ``frames`` samples or the ring is empty.
        while written < frames:
            try:
                block = self._ring.get_nowait()
            except queue.Empty:
                break
            need = frames - written
            if block.shape[0] <= need:
                out[written:written + block.shape[0]] = block
                written += block.shape[0]
            else:
                out[written:] = block[:need]
                self._cb_carry = block[need:]
                written = frames
                break
        # Underrun: fill remainder with silence.
        if written < frames:
            out[written:].fill(0.0)
        # Apply gain in-place. Skip the multiply when unity for the
        # common case.
        if self._gain != 1.0:
            out *= self._gain
