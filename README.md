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
3. **Clean up** — soft-delete promotions/updates/concluded **email** (recoverable from Trash
   ~30 days), guarded by deterministic protection rules so important mail is never touched.
   WhatsApp cleanup is separate and off by default: only `PROMOTION` chats, only when
   `allow_whatsapp_delete: true`, and it clears a **whole conversation** (delete-for-me — see
   Safety). SMS is classified but never auto-deleted.
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
- **Email is soft-delete only** — Gmail/Outlook removals go to Trash, recoverable for ~30 days.
- **WhatsApp is the exception** — a WhatsApp cleanup is **delete-for-me of an entire
  conversation** and is **not recoverable** like Trash (it never affects the other person and
  is never delete-for-everyone). It needs a second opt-in beyond `dry_run`
  (`allow_whatsapp_delete: true`), applies only to `PROMOTION` chats, and is off by default.
- **Protected items never touched**, and **every deletion is logged** with sender,
  subject, reason, and id.

## License

MIT — see [LICENSE](LICENSE).
