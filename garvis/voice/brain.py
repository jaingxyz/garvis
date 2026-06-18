"""Turn a spoken question into a spoken answer.

Reads the freshest digest (the chief-of-staff briefing the pipeline already wrote)
plus a few run stats from the DB, hands them to qwen2.5 over Ollama HTTP, and asks
for a short, conversational, speakable reply. No new mail is fetched here — that's
what the 'refresh' intent (a live sweep) is for.
"""
from __future__ import annotations

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

_READ_CUES = ("read", "last message", "what did", "tell me about", "message from", "last from", "with ")

def is_read_recent(q: str) -> bool:
    ql = q.lower()
    return any(c in ql for c in _READ_CUES)

def is_send_email(q: str) -> bool:
    ql = q.lower()
    return any(c in ql for c in _SEND_CUES)

def extract_contact(q: str) -> str | None:
    """Extract contact name for targeted reads, e.g. 'last message from Alex' or 'read from a contact'.
    Returns the best guess name phrase or None. Simple word-based; good enough for voice."""
    ql = q.lower()
    for cue in _READ_CUES:
        if cue in ql:
            tail = ql.split(cue, 1)[-1].strip()
            # take first 1-3 words as name
            words = [w.strip(".,!?") for w in tail.split() if len(w) > 1][:3]
            if words:
                return " ".join(words)
    return None

_SEND_CUES = ("send email", "send a message", "reply to", "email", "message to")

def is_send_email(q: str) -> bool:
    ql = q.lower()
    return any(c in ql for c in _SEND_CUES)

def extract_send_params(cfg: VoiceConfig, q: str) -> dict:
    """Use LLM to extract to, subject, body for send email intent.
    Returns dict or {} on failure."""
    try:
        resp = requests.post(
            f"{cfg.ollama_url}/api/chat",
            json={
                "model": cfg.ollama_model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": "Extract for email send: to (use email if possible or name), subject, body. Return only JSON with keys to, subject, body."},
                    {"role": "user", "content": q},
                ],
            },
            timeout=30,
        )
        data = resp.json()["message"]["content"]
        return json.loads(data)
    except:
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

def trigger_send_email(cfg: VoiceConfig, to: str, subject: str, body: str) -> bool:
    """Live send email using the main venv's MCP client (gmail_send).
    This allows voice to perform real actions like "send email to relocation about the flag".
    """
    if not Path(cfg.main_python).exists():
        return False
    # Escape for shell -c (simple for demo; in prod use shlex or file)
    code = f'''
import asyncio
import sys
sys.path.insert(0, "{cfg.root}")
from garvis.mcp_client import connect
from garvis.config import Config
async def main():
    c = Config.load()
    tools = await connect(c)
    result = await tools.call("gmail_send", to="{to}", subject="{subject}", body="{body}")
    print("RESULT:", result)
asyncio.run(main())
'''
    try:
        out = subprocess.check_output(
            [str(cfg.main_python), "-c", code],
            cwd=str(cfg.root),
            text=True,
            stderr=subprocess.STDOUT,
        )
        print("[voice] send result:", out)
        return True
    except Exception as e:
        print("[voice] send failed:", e)
        return False

def trigger_read_last_text(cfg: VoiceConfig, contact: str) -> str:
    """Live read recent conversation from contact using google-messages MCP via main venv.
    Allows voice 'read last from X' or 'a message from a contact' to get fresh data.
    """
    if not Path(cfg.main_python).exists():
        return "Main pipeline not available for live read."
    code = f'''
import asyncio
import sys
import json
sys.path.insert(0, "{cfg.root}")
from garvis.mcp_client import connect
from garvis.config import Config
async def main():
    c = Config.load()
    tools = await connect(c)
    results = await tools.call("search_messages", query="{contact}", limit=1)
    if results:
        name = results[0].get("name") if isinstance(results, list) else results.get("name")
        if name:
            msgs = await tools.call("read_conversation", name=name, limit=3)
            print(json.dumps(msgs))
            return
    print("[]")
asyncio.run(main())
'''
    try:
        out = subprocess.check_output(
            [str(cfg.main_python), "-c", code],
            cwd=str(cfg.root),
            text=True,
            stderr=subprocess.STDOUT,
        )
        msgs = json.loads(out)
        if msgs:
            last = msgs[-1]
            return f"Last from {contact}: {last.get('text', last)}"
        return f"No recent messages found from {contact}."
    except Exception as e:
        return f"Couldn't read live from {contact}: {e}"


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
        params = extract_send_params(cfg, question)
        if params:
            ok = trigger_send_email(cfg, params.get("to", "unknown"), params.get("subject", "Update from Garvis"), params.get("body", ""))
            return f"{'Sent' if ok else 'Failed to send'} the email about {params.get('subject', 'the topic')}."
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
