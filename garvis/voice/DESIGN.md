# Garvis Voice — design

A local, always-on Mac voice interface for Garvis. Say "Garvis", ask a question
("what's open?", "what dates do you have for me?", "who am I waiting on?"), and it
answers out loud from the latest briefing + live loop state. Fully local.

## Stack
- **Wake word:** "Whisper always-on" — a tiny faster-whisper model transcribes a rolling
  window continuously and triggers on "garvis" (fuzzy, since whisper mishears the name).
  No wake-word library or account. (Swappable for Porcupine/openWakeWord later.)
- **STT:** faster-whisper, `tiny.en` for the wake-listen + `base.en` for the command.
  int8/CPU — ctranslate2 has no Metal backend, but these are fast on Apple silicon.
  Models load `local_files_only` after the first download → zero network per run.
- **Brain:** reads the freshest digest + live open loops + run stats, hands them to
  qwen2.5 over Ollama HTTP, asks for a short speakable reply.
- **TTS:** macOS `say` (zero-dep). Swap Piper/Kokoro for a neural voice without touching
  the rest.

It runs in its own Python 3.12 venv (`.venv-voice`) because the audio/ML wheels lag newer
Python. The voice package reads `digests/` + `state/garvis.db` directly and calls Ollama
over HTTP — it does **not** import the langchain-bound `garvis` package.

## Modules
```
garvis/voice/
  config.py   VoiceConfig; reads config.yaml (llm + paths) + optional `voice:` overrides
  audio.py    Mic: shared 16k mono stream; rolling wake windows; record-until-silence VAD
  stt.py      Transcriber: faster-whisper tiny (wake) + base (command); fuzzy heard_wake()
  brain.py    digest + live loops + run stats -> Ollama -> spoken answer; intent routing
  state.py    read open loops; fuzzy mark_done / snooze by spoken phrase (stdlib sqlite3)
  tts.py      speak() / chime() via `say`
  daemon.py   main loop: wake -> ack -> record -> transcribe -> route -> say
  __main__.py entrypoint  ->  python -m garvis.voice
```

## Intent routing (`brain.route`)
- **refresh / "check now"** → launch a live sweep in the background, announce it.
- **snooze** ("remind me … tomorrow") → set the matched loop to snoozed.
- **mark done** ("I already replied to …") → close the matched loop.
- **otherwise** → answer the question from the digest + live loops via the LLM.

Live open loops are fed as **authoritative** context above the (possibly stale) digest
text, so the spoken answer reflects current state, not a frozen snapshot.

## Notes / ideas
- Tune `rms_threshold` to your room/mic; consider silero-VAD if the energy gate is noisy.
- Always-on whisper costs CPU and can false-trigger — swap the wake layer if it annoys.
- Conversation mode (follow-ups without re-saying the wake word) and richer live-fetch
  intents ("read the last message from <contact>") are natural extensions.
