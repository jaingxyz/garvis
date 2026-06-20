# Garvis

A **local, edge-only** personal triage + chief-of-staff assistant. Garvis sweeps your
**Gmail**, **Outlook**, **text messages** (Google Messages), and optionally **WhatsApp**
through local MCP servers, triages everything, cleans out the noise, hands you a prioritized
briefing — and can talk to you out loud.

Everything runs on your machine: a local **Ollama** model does the judgment, local **MCP
servers** do the I/O, and (optionally) a local **faster-whisper + `say`** voice daemon
lets you ask "Garvis, what's open?" Nothing is sent to a third-party LLM.

## What it does each run

1. **Gather** new mail/texts (and WhatsApp, if enabled) since the last run.
2. **Classify** every item — `PROMOTION` / `UPDATE` / `ACTIONABLE` / `PERSONAL` /
   `WAITING` / `CONCLUDED` / `UNSURE` — governed by your `config/rules.md`.
3. **Clean up** — soft-delete promotions/updates/concluded threads (recoverable ~30 days),
   guarded by deterministic protection rules so important mail is never touched.
4. **Prioritize** what's left into a ranked, chief-of-staff briefing.
5. **Deliver** a dated digest to `digests/` and email a copy to you.

It remembers across runs (SQLite): a recoverable deletion audit, **sticky loops** (a
settled thread can't be silently reopened by a bad run), and a small **memory graph** of
the people and context in your life that sharpens triage.

## Design principle

**The LLM never drives tool calls.** Python orchestrates MCP calls deterministically; the
local model only classifies, summarizes, and prioritizes. Deterministic safety guards
(VIP senders, protected keywords, OTP grace windows) live in code, never in the model.

## Quick start

```bash
cp config.example.yaml config.yaml                 # then edit with your values
cp config/profile.example.yaml config/profile.yaml # optional: the memory graph
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
ollama pull qwen2.5:14b                             # local judgment model

python -m garvis.run --check                        # connectivity check
python -m garvis.run                                # full sweep (DRY-RUN by default)
```

You also need the local MCP servers it talks to — **personal-gmail**, **personal-outlook**,
and **google-messages**, plus optional **whatsapp-mcp** — cloned and built, with their
paths set in `config.yaml` (WhatsApp is off by default; enable it under `mcp_servers`).

See **[STANDALONE.md](STANDALONE.md)** for the architecture, full setup, the voice daemon,
and the durable-state / memory-graph design.

## Safety

- **Dry-run by default** — classifies and writes the digest but deletes nothing until you
  set `dry_run: false`.
- **Soft-delete only** — everything is recoverable from Trash for ~30 days.
- **Protected items never touched**, and **every deletion is logged** with sender,
  subject, reason, and id.

## License

MIT — see [LICENSE](LICENSE).
