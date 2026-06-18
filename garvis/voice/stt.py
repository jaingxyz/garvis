"""Speech-to-text via faster-whisper. Two model sizes: a tiny one runs continuously
to catch the wake word cheaply; a base one transcribes the actual question accurately."""
from __future__ import annotations

import numpy as np
from faster_whisper import WhisperModel

from .config import VoiceConfig


class Transcriber:
    def __init__(self, cfg: VoiceConfig):
        self.cfg = cfg
        # int8 on CPU — ctranslate2 has no Metal backend, but tiny/base are fast on M-series.
        self.wake = self._load(cfg.wake_model)
        self.command = self._load(cfg.command_model)

    @staticmethod
    def _load(name: str) -> WhisperModel:
        """Load fully offline from the local cache. Only the very first time a model is
        missing do we reach the network to download it; every run after is zero-network."""
        try:
            return WhisperModel(name, device="cpu", compute_type="int8",
                                local_files_only=True)
        except Exception:  # not cached yet — one-time download, then it's local forever
            return WhisperModel(name, device="cpu", compute_type="int8")

    def _run(self, model: WhisperModel, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        segments, _ = model.transcribe(
            audio, language="en", beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text for s in segments).strip()

    def heard_wake(self, audio: np.ndarray) -> bool:
        text = self._run(self.wake, audio).lower()
        if not text:
            return False
        # whisper mishears the name a lot; match the family + the distinctive "arvis" tail.
        if "arvis" in text:
            return True
        return any(w in text for w in self.cfg.wake_words)

    def transcribe_command(self, audio: np.ndarray) -> str:
        return self._run(self.command, audio)
