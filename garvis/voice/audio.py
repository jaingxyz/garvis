"""Microphone capture for the voice daemon.

Two jobs:
  - stream_chunks(): a rolling generator of fixed-length windows, fed to the wake
    listener so it can transcribe continuously and catch "Garvis".
  - record_command(): after the wake word, capture one utterance — wait for speech
    to start, then stop after a beat of trailing silence (energy-based VAD).
"""
from __future__ import annotations

import queue
from collections.abc import Iterator

import numpy as np
import sounddevice as sd


def _rms(frames: np.ndarray) -> float:
    if frames.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frames))))


class Mic:
    """Single shared 16 kHz mono input stream (float32)."""

    def __init__(self, sample_rate: int = 16000, block_ms: int = 100):
        self.sr = sample_rate
        self.block = int(sample_rate * block_ms / 1000)
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="float32",
            blocksize=self.block, callback=self._cb,
        )

    def _cb(self, indata, frames, time_info, status):  # noqa: ANN001
        self._q.put(indata[:, 0].copy())

    def __enter__(self) -> "Mic":
        self._stream.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stream.stop()
        self._stream.close()

    def _blocks(self) -> Iterator[np.ndarray]:
        while True:
            yield self._q.get()

    def stream_chunks(self, window_seconds: float) -> Iterator[np.ndarray]:
        """Yield overlapping ~window_seconds buffers for continuous wake detection."""
        need = int(self.sr * window_seconds)
        buf = np.zeros(0, dtype=np.float32)
        for b in self._blocks():
            buf = np.concatenate([buf, b])
            if len(buf) >= need:
                yield buf[-need:].copy()
                buf = buf[-self.block:]  # keep a touch of tail, drop the rest

    def drain(self) -> None:
        """Discard any queued audio (e.g. the wake word itself) before recording."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def record_command(self, *, max_seconds: float, silence_trailing: float,
                       rms_threshold: float) -> np.ndarray:
        """Capture one utterance with simple start/stop VAD. Returns float32 @ sr."""
        self.drain()
        block_dur = self.block / self.sr
        collected: list[np.ndarray] = []
        started = False
        silence = 0.0
        elapsed = 0.0
        lead_grace = 2.5  # allow this long for speech to begin before giving up
        for b in self._blocks():
            elapsed += block_dur
            loud = _rms(b) >= rms_threshold
            if not started:
                if loud:
                    started = True
                    collected.append(b)
                elif elapsed >= lead_grace:
                    return np.zeros(0, dtype=np.float32)  # nobody spoke
                continue
            collected.append(b)
            silence = 0.0 if loud else silence + block_dur
            if silence >= silence_trailing or elapsed >= max_seconds:
                break
        return np.concatenate(collected) if collected else np.zeros(0, dtype=np.float32)
