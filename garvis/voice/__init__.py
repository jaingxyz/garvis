"""Garvis voice layer — an always-on local mic daemon.

Self-contained: reads digests/ + state/garvis.db directly and calls Ollama over
HTTP. Does NOT import the langchain-bound garvis package, so its audio/ML deps
(faster-whisper, sounddevice) live in their own .venv-voice (Python 3.12).

Run:  ./.venv-voice/bin/python -m garvis.voice
"""
