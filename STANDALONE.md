# Garvis standalone (LangChain + Ollama)

Garvis runs as a plain Python app — no hosted LLM, no Claude runtime. It launches your
local MCP servers over stdio and uses a local Ollama model for the judgment steps.

## Architecture

```
run.py orchestrator (Python drives everything)
  ├─ mcp_client.py   MultiServerMCPClient → launches the local node MCP servers
  │                  (personal-gmail, personal-outlook, google-messages, whatsapp) over stdio
  ├─ gather.py       pull mail/texts via MCP tools → normalized Items
  │                  + thread-state check (who sent the last message?)
  ├─ classify.py     Ollama labels each item: PROMOTION/UPDATE/ACTIONABLE/
  │                  PERSONAL/WAITING/CONCLUDED/UNSURE  (governed by config/rules.md)
  ├─ guards.py       deterministic protection (VIP, keywords, OTP grace) — never the LLM
  ├─ prioritize.py   Ollama ranks ACTIONABLE/PERSONAL into a chief-of-staff briefing
  ├─ actions.py      clean up deletable items: email soft-delete + opt-in WhatsApp (guarded)
  ├─ store.py        SQLite: run history, deletion audit, sticky loops, memory graph
  └─ digest.py       render markdown digest, write to digests/, email a copy
```

Design choice: **the LLM never drives tool calls** (local models are unreliable at
tool-calling). Python calls MCP tools deterministically; the model only classifies,
summarizes, and prioritizes.

## Setup

```bash
cp config.example.yaml config.yaml      # then edit config.yaml with your own values
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Ollama must be running (`ollama serve`). Pull a model: `ollama pull qwen2.5:14b`. A 14b
model follows the "never invent dates" / grouping rules far more reliably than 7b.

You also need the three local MCP servers cloned and built, with their paths set in
`config.yaml` under `mcp_servers`.

## Run

```bash
. .venv/bin/activate
python -m garvis.run --check       # connect to MCP servers + ping the LLM, then exit
python -m garvis.run               # full pipeline (DRY-RUN by default — deletes nothing)
python -m garvis.run --no-email    # full pipeline, skip emailing the digest
python -m garvis.run --delta       # fast recent-only sweep (quick voice back-and-forth)
python -m garvis.run --continuous  # background worker: loop delta sweeps to pre-compute
python -m garvis.run --status      # print the latest digest (no scan)
python -m garvis.run --history     # show run history + deletion audit log
```

Sources: Gmail, Outlook, Google Messages, and (optional) WhatsApp — enable each under
`mcp_servers` in `config.yaml`.

## Safety / config (`config.yaml`)

- `dry_run: true` (default) — classifies and writes the digest but **deletes nothing**.
  Flip to `false` only once you trust it.
- `vip_senders` / `protected_keywords` — deterministic protection enforced in
  `guards.py`, independent of the LLM. Protected items are never deleted.
- `otp_grace_minutes` — one-time/verification codes are protected only while fresh.
- `mcp_servers.<name>.enabled` — `google-messages` drives a Chromium profile; only one
  client can hold it at a time.
- `scan_limits` / `window_days` — caps per account and how far back to look.

## Durable state & memory graph

`state/garvis.db` (SQLite) gives Garvis memory:
- **runs / actions / seen** — run history, a recoverable deletion audit, and dedupe.
- **loops** — one row per open thread with a **sticky-done rule**: a sweep can't reopen
  a settled thread unless a genuinely newer inbound message arrives (stops a single
  mis-judged run from resurrecting closed threads).
- **entities / relations** — a small knowledge graph (people, roles, and edges like
  `self → spouse_of → …`, `self → relocating_to → …`) seeded from `config/profile.yaml`,
  injected into the classifier/prioritizer prompts so triage understands your world.

## Voice (`garvis/voice/`)

An always-on local mic daemon: say **"Garvis"**, then ask ("what's open?", "what dates do
you have for me?"). Fully local — faster-whisper (STT) → qwen2.5 (Ollama) → macOS `say`
(TTS). It reads `digests/` + `state/garvis.db` directly and talks to Ollama over HTTP, so
it needs its **own Python 3.12 venv** (the audio/ML wheels lag newer Python):

```bash
brew install python@3.12 portaudio    # ffmpeg also required (whisper)
python3.12 -m venv .venv-voice
./.venv-voice/bin/pip install -r requirements-voice.txt

./.venv-voice/bin/python -m garvis.voice --ask "what's open?"   # text test, no mic
./.venv-voice/bin/python -m garvis.voice                        # live always-on daemon
```

Most questions read the cached digest + live loops instantly (offline). Saying "refresh"
runs a live sweep; "mark … done" / "remind me … tomorrow" mutate loops; "read the last
message from …" pulls a thread live. It can also **send email** by voice, but that's
**off by default** — set `voice.allow_voice_send: true` in `config.yaml` to enable it, so
a misheard command can never fire mail. Conversation mode keeps listening for follow-ups
for a few seconds after each answer. Run it as a login agent via the bundled
`garvis/voice/com.garvis.voice.plist` (edit the paths first).

## Schedule it

```bash
# crontab -e  — 7am & 4pm daily (edit the path to your checkout)
0 7,16 * * * cd /path/to/garvis && ./.venv/bin/python -m garvis.run >> logs/cron.log 2>&1
```
