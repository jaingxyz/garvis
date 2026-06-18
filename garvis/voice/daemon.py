"""The always-on loop: listen for "Garvis" -> capture the question -> answer out loud.

  wake (tiny whisper, continuous)  ->  ack  ->  record (VAD)  ->  base whisper
       ->  intent (refresh? read?) ->  qwen2.5  ->  say
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import brain, state, tts
from .audio import Mic
from .config import VoiceConfig, load
from .stt import Transcriber


def _log(cfg: VoiceConfig, msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(cfg.log_path, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def handle(cfg: VoiceConfig, stt: Transcriber, mic: Mic) -> None:
    """Handle one wake -> question -> answer cycle.
    Conversation mode: after answering, stay listening for follow-ups for a short window
    without requiring re-wake. This makes it feel more like a natural agent.
    """
    tts.chime(cfg)
    audio = mic.record_command(
        max_seconds=cfg.command_max_seconds,
        silence_trailing=cfg.silence_trailing,
        rms_threshold=cfg.rms_threshold,
    )
    if audio.size == 0:
        _log(cfg, "no question heard")
        return
    question = stt.transcribe_command(audio)
    if not question:
        _log(cfg, "empty transcription")
        return
    _log(cfg, f"Q: {question}")
    reply = brain.route(cfg, question)
    _log(cfg, f"A: {reply}")
    tts.speak(cfg, reply)

    # Conversation mode: listen for follow-ups for N seconds without wake word.
    # User can keep talking naturally after the answer.
    convo_seconds = getattr(cfg, "conversation_mode_seconds", 25)
    if convo_seconds > 0:
        _log(cfg, f"listening for follow-up ({convo_seconds}s)...")
        deadline = time.time() + convo_seconds
        while time.time() < deadline:
            audio = mic.record_command(
                max_seconds=cfg.command_max_seconds,
                silence_trailing=cfg.silence_trailing,
                rms_threshold=cfg.rms_threshold,
            )
            if audio.size == 0:
                continue
            follow_up = stt.transcribe_command(audio)
            if follow_up:
                _log(cfg, f"Q (follow-up): {follow_up}")
                reply = brain.route(cfg, follow_up)
                _log(cfg, f"A: {reply}")
                tts.speak(cfg, reply)
                # extend window a bit after each exchange
                deadline = max(deadline, time.time() + 12)
            else:
                break  # no speech, end convo mode
        _log(cfg, "conversation mode ended")


def ask_once(cfg: VoiceConfig, question: str, *, speak: bool) -> str:
    """Text-in path — route a typed question (no mic). For testing the brain."""
    reply = brain.route(cfg, question)
    if speak:
        tts.speak(cfg, reply)
    return reply


def main() -> int:
    ap = argparse.ArgumentParser(prog="garvis.voice")
    ap.add_argument("--ask", metavar="TEXT",
                    help="answer one typed question and exit (no mic) — brain smoke test")
    ap.add_argument("--speak", action="store_true",
                    help="with --ask, also speak the reply aloud")
    ap.add_argument("--greet", action="store_true",
                    help="speak boot greeting + quick status and exit (useful for dev tests)")
    args = ap.parse_args()

    cfg = load()
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    if args.greet:
        # Manual boot greeting for quick dev tests (no full daemon or mic needed)
        try:
            greeting = "Hello. This is Garvis on startup. "
            loops = state.open_loops(cfg)
            if loops:
                top = loops[0]
                title = top["title"] if isinstance(top, dict) else top["title"]
                who = top["who"] if isinstance(top, dict) else top["who"]
                update = f"You have {len(loops)} open or waiting items. The top one is {title or 'something'} with {who or 'someone'}."
            else:
                update = "No open items at the moment."
            full = greeting + "Would you like an update? " + update
            print(full)
            tts.speak(cfg, full)
        except Exception as e:
            print(f"greeting error: {e}")
        return 0
    if args.ask:
        print(ask_once(cfg, args.ask, speak=args.speak))
        return 0
    _log(cfg, f"Garvis voice starting (wake={cfg.wake_model}, cmd={cfg.command_model}, "
              f"llm={cfg.ollama_model}). Loading models…")
    stt = Transcriber(cfg)
    _log(cfg, "models loaded. Listening for 'Garvis'… (Ctrl-C to stop)")

    # Startup greeting + quick status on boot/login or daemon start.
    # Greets, asks about update, and provides one automatically.
    # Very useful for quick live tests during development.
    try:
        _log(cfg, "delivering startup greeting + quick status")
        greeting = "Hello. This is Garvis on startup. "
        loops = state.open_loops(cfg)
        if loops:
            top = loops[0]
            title = top["title"] if isinstance(top, dict) else top["title"]
            who = top["who"] if isinstance(top, dict) else top["who"]
            update = f"You have {len(loops)} open or waiting items. The top one is {title or 'something'} with {who or 'someone'}."
        else:
            update = "No open items at the moment."
        full_greeting = greeting + "Would you like an update? " + update
        tts.speak(cfg, full_greeting)
        _log(cfg, f"startup greeting complete: {full_greeting[:120]}...")
    except Exception as e:
        _log(cfg, f"startup greeting error (ignored): {e}")

    try:
        with Mic(sample_rate=cfg.sample_rate) as mic:
            last = 0.0
            chunk_count = 0
            proactive_interval = getattr(cfg, "proactive_check_chunks", 180)  # ~5 min at 1.6s chunks
            for chunk in mic.stream_chunks(cfg.wake_chunk_seconds):
                chunk_count += 1
                if stt.heard_wake(chunk):
                    now = time.time()
                    if now - last < 2.0:      # debounce repeated detections
                        continue
                    last = now
                    _log(cfg, "wake word detected")
                    handle(cfg, stt, mic)
                    _log(cfg, "listening…")
                    chunk_count = 0  # reset after interaction

                # Proactive nudge: every N chunks, check for new high-priority open loops
                # and speak a brief summary if any (edge-only, no extra deps).
                if chunk_count > 0 and chunk_count % proactive_interval == 0:
                    try:
                        loops = state.open_loops(cfg)  # reuse state for open loops
                        if loops:
                            # simple spoken nudge from the highest priority (first in list)
                            top = loops[0]
                            nudge = f"Quick note: you still have {top['status']} items, including {top['title']} with {top['who'] or 'someone'}."
                            _log(cfg, f"proactive: {nudge}")
                            tts.speak(cfg, nudge)
                    except Exception as e:
                        _log(cfg, f"proactive check error (ignored): {e}")
                    chunk_count = 0  # avoid spam
    except KeyboardInterrupt:
        _log(cfg, "stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
