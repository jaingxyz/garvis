"""Text-to-speech via macOS `say`. Zero dependencies; swap in Piper/Kokoro later
for a richer neural voice without touching the rest of the daemon."""
from __future__ import annotations

import subprocess

from .config import VoiceConfig


def speak(cfg: VoiceConfig, text: str) -> None:
    if not text:
        return
    subprocess.run(
        ["say", "-v", cfg.tts_voice, "-r", str(cfg.tts_rate), text],
        check=False,
    )


def chime(cfg: VoiceConfig) -> None:
    """Short spoken ack so you know Garvis is listening for your question."""
    subprocess.run(["say", "-v", cfg.tts_voice, "-r", str(cfg.tts_rate), "Yes?"],
                   check=False)
