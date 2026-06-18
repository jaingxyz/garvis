"""Voice-daemon settings. Reuses the repo's config.yaml for the LLM + data paths,
adds a few voice-only knobs (optionally overridable via config.yaml's `voice:` block)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# repo root = .../garvis (two levels up from this file: garvis/voice/config.py)
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class VoiceConfig:
    # paths
    root: Path = ROOT
    digests_dir: Path = ROOT / "digests"
    db_path: Path = ROOT / "state" / "garvis.db"
    log_path: Path = ROOT / "logs" / "voice.log"
    main_python: Path = ROOT / ".venv" / "bin" / "python"  # to shell out for a live sweep

    # LLM (mirrors config.yaml -> llm)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:14b"

    # speech-to-text (faster-whisper, CPU/int8 — plenty fast on Apple silicon)
    wake_model: str = "tiny.en"     # tiny: cheap, runs continuously to catch the wake word
    command_model: str = "base.en"  # base: more accurate, transcribes the actual question
    sample_rate: int = 16000

    # wake word — we transcribe continuously and trigger on any of these (whisper mishears)
    wake_words: tuple[str, ...] = ("garvis", "jarvis", "carvis", "garviss", "gar vis")

    # capture tuning
    wake_chunk_seconds: float = 1.6     # rolling window the wake-listener transcribes
    command_max_seconds: float = 12.0   # hard cap on a single question
    silence_trailing: float = 1.1       # stop the question after this much trailing silence
    rms_threshold: float = 0.012        # speech vs silence energy gate (0..1 float audio)

    # text-to-speech (macOS `say`)
    tts_voice: str = "Daniel"           # British male; closest built-in "Jarvis" feel
    tts_rate: int = 190                 # words per minute

    # intents
    refresh_phrases: tuple[str, ...] = (
        "refresh", "check now", "scan now", "sweep", "run garvis", "check my", "update now",
    )

    # polish & agentic extensions (overridable in config.yaml voice: block)
    conversation_mode_seconds: int = 25   # listen for follow-ups after answer (0 to disable)
    proactive_check_chunks: int = 180     # ~every 5 min at default chunk size; 0 to disable nudges
    proactive_min_priority: str = "open"  # only nudge for 'open' or higher (stub for future)

    def load_overrides(self) -> "VoiceConfig":
        cfgfile = self.root / "config.yaml"
        if not cfgfile.exists():
            return self
        raw = yaml.safe_load(cfgfile.read_text()) or {}
        llm = raw.get("llm", {})
        if llm.get("model"):
            self.ollama_model = llm["model"]
        if llm.get("base_url"):
            self.ollama_url = llm["base_url"]
        v = raw.get("voice", {}) or {}
        for k in ("wake_model", "command_model", "tts_voice", "tts_rate",
                  "rms_threshold", "ollama_model",
                  "conversation_mode_seconds", "proactive_check_chunks"):
            if k in v:
                setattr(self, k, v[k])
        if "wake_words" in v:
            self.wake_words = tuple(v["wake_words"])
        return self


def load() -> VoiceConfig:
    return VoiceConfig().load_overrides()
