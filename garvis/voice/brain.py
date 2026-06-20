"""Turn a spoken question into a spoken answer.

Reads the freshest digest (the chief-of-staff briefing the pipeline already wrote)
plus a few run stats from the DB, hands them to qwen2.5 over Ollama HTTP, and asks
for a short, conversational, speakable reply. No new mail is fetched here — that's
what the 'refresh' intent (a live sweep) is for.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import requests

from . import state
from .config import VoiceConfig

SYSTEM = """You are Garvis, the user's sharp personal chief of staff, answering OUT LOUD.
Your reply is read by a text-to-speech voice, so:
- Speak in 1-4 short sentences. Conversational, warm, direct. No preamble.
- Plain spoken English only: NO markdown, NO bullet symbols, NO asterisks, NO links,
  no "here's a list". If you must enumerate, say "first... second..." naturally.
- Answer ONLY from the briefing below. Never invent dates, names, or facts. If the
  briefing doesn't cover what was asked, say so briefly.
- Refer to the user as "you". Refer to yourself as "I" / Garvis.
"""


def latest_digest(cfg: VoiceConfig) -> str:
    files = sorted(Path(cfg.digests_dir).glob("20*.md"))
    if not files:
        return ""
    return files[-1].read_text()


def run_stats(cfg: VoiceConfig) -> str:
    if not Path(cfg.db_path).exists():
        return ""
    try:
        db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        r = db.execute(
            "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
    except sqlite3.Error:
        return ""
    if not r:
        return ""
    return (f"Last sweep #{r['id']} at {(r['finished_at'] or '')[:16]} ({r['mode']}): "
            f"scanned {r['scanned']}, {r['threads']} threads, "
            f"{r['deleted']} cleaned, {r['flagged']} flagged unsure.")


def is_refresh(question: str, cfg: VoiceConfig) -> bool:
    q = question.lower()
    return any(p in q for p in cfg.refresh_phrases)


_DONE_CUES = ("mark", "done", "handled", "resolved", "already replied",
              "already responded", "took care", "finished", "completed", "close")
_SNOOZE_CUES = ("snooze", "remind me", "later", "tomorrow", "next week", "not now")


def is_mark_done(q: str) -> bool:
    ql = q.lower()
    return any(c in ql for c in _DONE_CUES)


def is_snooze(q: str) -> bool:
    ql = q.lower()
    return any(c in ql for c in _SNOOZE_CUES)

# Intent cues. Kept specific so a read/answer question can't trip a live action:
# "what's in my email" must NOT look like "send email".
# Substring cues, chosen to avoid collisions ("mail to" must not match "email today").
_READ_CUES = ("read the last", "last message", "last text", "message from",
              "last from", "what did")
_SEND_CUES = ("send email", "send an email", "send a message", "send a mail",
              "send a text", "compose email", "compose a message", "reply to")


def is_read_recent(q: str) -> bool:
    return any(c in q.lower() for c in _READ_CUES)


def is_send_email(q: str) -> bool:
    return any(c in q.lower() for c in _SEND_CUES)


def extract_contact(q: str) -> str | None:
    """Best-effort contact name for a targeted read, e.g. 'last message from Alex'."""
    ql = q.lower()
    for cue in _READ_CUES:
        if cue in ql:
            tail = ql.split(cue, 1)[-1].strip()
            words = [w.strip(".,!?") for w in tail.split() if len(w) > 1][:3]
            if words:
                return " ".join(words)
    return None


def extract_send_params(cfg: VoiceConfig, q: str) -> dict:
    """Use the LLM to extract {to, subject, body} for a send-email intent. {} on failure."""
    try:
        resp = requests.post(
            f"{cfg.ollama_url}/api/chat",
            json={
                "model": cfg.ollama_model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": "Extract for email send: to (use email "
                     "if possible or name), subject, body. Return only JSON with keys to, "
                     "subject, body."},
                    {"role": "user", "content": q},
                ],
            },
            timeout=30,
        )
        return json.loads(resp.json()["message"]["content"])
    except (requests.RequestException, KeyError, ValueError):
        return {}


def trigger_sweep(cfg: VoiceConfig) -> bool:
    """Kick off a live pipeline run in the background (the main 3.14 venv)."""
    if not Path(cfg.main_python).exists():
        return False
    subprocess.Popen(
        [str(cfg.main_python), "-m", "garvis.run", "--no-email"],
        cwd=str(cfg.root),
        stdout=open(cfg.log_path, "a"), stderr=subprocess.STDOUT,
    )
    return True

# A live MCP action runs in the main venv. The script is a CONSTANT — all caller/voice
# values are passed as JSON on stdin and parsed as DATA, never interpolated into code.
# (The old version f-string-interpolated voice text into `python -c`, a code-injection
# hole: a contact name or body with quotes could execute arbitrary code.)
_ACTION_SCRIPT = r"""
import asyncio, json, sys
from garvis.mcp_client import connect
from garvis.config import Config

req = json.load(sys.stdin)

async def main():
    tools = await connect(Config.load())
    action = req["action"]
    if action == "send_email":
        r = await tools.call("gmail_send", to=req["to"], subject=req["subject"],
                             body=req["body"])
        print(json.dumps({"ok": True, "result": str(r)}))
    elif action == "read_last":
        hits = await tools.call("search_messages", query=req["contact"], limit=1)
        name = None
        if hits:
            first = hits[0] if isinstance(hits, list) else hits
            name = first.get("name") if isinstance(first, dict) else None
        msgs = await tools.call("read_conversation", name=name, limit=3) if name else []
        print(json.dumps({"ok": True, "messages": msgs}))

asyncio.run(main())
"""


def _run_main_action(cfg: VoiceConfig, payload: dict) -> dict:
    """Run a live MCP action in the main venv, passing payload as stdin JSON. {} on error."""
    if not Path(cfg.main_python).exists():
        return {}
    env = {**os.environ, "PYTHONPATH": str(cfg.root)}
    try:
        proc = subprocess.run(
            [str(cfg.main_python), "-c", _ACTION_SCRIPT],
            input=json.dumps(payload), cwd=str(cfg.root), env=env,
            text=True, capture_output=True, timeout=60,
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return {}


def trigger_send_email(cfg: VoiceConfig, to: str, subject: str, body: str) -> bool:
    """Live-send an email via the main venv's MCP client (gmail_send)."""
    res = _run_main_action(cfg, {"action": "send_email", "to": to,
                                 "subject": subject, "body": body})
    return bool(res.get("ok"))


def trigger_read_last_text(cfg: VoiceConfig, contact: str) -> str:
    """Live-read the most recent messages from a contact (read-only)."""
    res = _run_main_action(cfg, {"action": "read_last", "contact": contact})
    if not res.get("ok"):
        return f"Couldn't read live from {contact}."
    msgs = res.get("messages") or []
    if msgs:
        last = msgs[-1]
        text = last.get("text", last) if isinstance(last, dict) else last
        return f"Last from {contact}: {text}"
    return f"No recent messages found from {contact}."


def route(cfg: VoiceConfig, question: str) -> str:
    """Single entry point: pick the intent and produce the spoken reply.
    Now supports richer agentic intents: refresh, mark_done, snooze, and read_recent from contact.
    Falls back to grounded answer using live loops + digest.
    """
    if is_refresh(question, cfg):
        ok = trigger_sweep(cfg)
        return ("On it — sweeping your email and texts now. Ask me again in a minute for "
                "the updated briefing." if ok
                else "I couldn't start a sweep; my main pipeline isn't reachable.")
    if is_snooze(question):
        title = state.snooze(cfg, question)
        if title:
            return f"Done — I've snoozed {title} and will resurface it later."
        # fall through to a normal answer if nothing matched
    if is_mark_done(question):
        title = state.mark_done(cfg, question)
        if title:
            return f"Got it — I've marked {title} as done. It won't show up again."
        # nothing matched a live loop; treat as a question
    contact = extract_contact(question) if is_read_recent(question) else None
    if is_read_recent(question) and contact:
        text = trigger_read_last_text(cfg, contact)
        if text and "Couldn't" not in text and "No recent" not in text:
            return text
        # fall to LLM with hint
    if is_send_email(question):
        if not getattr(cfg, "allow_voice_send", False):
            return ("Sending email by voice is turned off. Enable allow_voice_send in "
                    "config to let me send.")
        params = extract_send_params(cfg, question)
        if params and params.get("to"):
            ok = trigger_send_email(cfg, params["to"],
                                    params.get("subject", "Update from Garvis"),
                                    params.get("body", ""))
            return (f"{'Sent' if ok else 'Failed to send'} the email about "
                    f"{params.get('subject', 'the topic')}.")
        return "Couldn't parse the send request."
    return answer(cfg, question, contact=contact)


def answer(cfg: VoiceConfig, question: str, contact: str | None = None) -> str:
    """Produce spoken answer. If contact provided (from read_recent intent), we include
    a targeted hint in the prompt so the model can pull the relevant thread from context.
    """
    digest = latest_digest(cfg)
    if not digest:
        return "I don't have a briefing yet. Ask me to check now and I'll run a sweep."
    loops = state.loops_context(cfg)
    contact_hint = f"\n\nUser is asking specifically about recent messages with: {contact}" if contact else ""
    context = (f"{run_stats(cfg)}\n\n{loops}\n\n=== LATEST BRIEFING (background; may be "
               f"stale — defer to the live open loops above) ===\n{digest}{contact_hint}")
    try:
        resp = requests.post(
            f"{cfg.ollama_url}/api/chat",
            json={
                "model": cfg.ollama_model,
                "stream": False,
                "options": {"temperature": 0.2},
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user",
                     "content": f"My question: {question}\n\n{context}"},
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except (requests.RequestException, KeyError, ValueError) as e:
        return f"I couldn't reach my language model. {type(e).__name__}."
