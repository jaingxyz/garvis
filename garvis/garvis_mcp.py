"""Tiny MCP server to expose Garvis as a tool (per review nice-to-have).

This makes Garvis callable from Grok TUI or other agents as first-class tools,
instead of only via natural language prompts. Keeps everything deterministic
(Python orchestrates; LLM only for judgment in main pipeline).

Usage (as local MCP in ~/.grok/config.toml or Garvis voice):
  command = "python"
  args = ["-m", "garvis.garvis_mcp"]

Tools exposed (expand as needed):
- garvis_run_sweep(dry_run=True): Launch full sweep in background (returns fast).
  Use get_* tools to poll results. Avoids stdio transport timeouts on long runs.
- garvis_get_latest_digest(): Read most recent digest md.
- garvis_get_open_loops(): List current open/waiting from DB.
- garvis_mark_done(title_snippet): Update loop status (fuzzy match).

All local/edge-only. Uses same config as main Garvis.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import Config
from .store import Store

mcp = FastMCP("garvis")

# Resolve the Garvis project root (the dir containing config.yaml) so the
# background subprocess for sweeps finds the right config, logs, state, etc.
def _find_project_root() -> Path:
    p = Path(__file__).resolve()
    for _ in range(6):
        if (p / "config.yaml").exists():
            return p
        p = p.parent
    # Fallback (development layout)
    return Path(__file__).resolve().parent.parent

PROJECT_ROOT = _find_project_root()

# Reuse main config
CFG = Config.load()

@mcp.tool()
def garvis_run_sweep(dry_run: bool = True) -> str:
    """Run a Garvis sweep in the *background*.

    The tool returns immediately so the stdio MCP transport does not time out.
    The actual work (gather from gmail/outlook/messages + LLM + digest) runs
    via a detached subprocess using the local CLI.

    Poll with garvis_get_latest_digest() or garvis_get_open_loops() after a minute
    or two to see results.

    Set dry_run=False only when you want real soft-deletes.
    """
    env = os.environ.copy()
    env["GARVIS_DRY_RUN"] = "true" if dry_run else "false"
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    python = sys.executable
    cmd = [python, "-m", "garvis.run", "--no-email"]

    log_path = PROJECT_ROOT / "logs" / "sweep-mcp.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "a") as logf:
        subprocess.Popen(
            cmd,
            env=env,
            cwd=str(PROJECT_ROOT),
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,   # detach (Unix)
            close_fds=True,
        )

    return (
        f"Sweep launched in background (dry_run={dry_run}). "
        "It will write a new digest and update loops. "
        "Use garvis_get_latest_digest() or garvis_get_open_loops() to check progress."
    )

@mcp.tool()
def garvis_get_latest_digest() -> str:
    """Return the full text of the most recent Garvis digest (markdown)."""
    digests = sorted(Path(CFG.path("digests")).glob("20*.md"))
    if not digests:
        return "No digests yet. Run a sweep first."
    latest = digests[-1]
    return latest.read_text()

@mcp.tool()
def garvis_get_open_loops() -> str:
    """Return JSON list of current open/waiting loops from durable state DB."""
    store = Store(CFG.path("db"))
    loops = store.open_loops()
    if not loops:
        return "[]"
    # Return simple list of dicts for the tool consumer
    result = [
        {
            "title": r["title"],
            "who": r["who"],
            "status": r["status"],
            "last_inbound_at": r["last_inbound_at"],
        }
        for r in loops
    ]
    return json.dumps(result, indent=2)

@mcp.tool()
def garvis_mark_done(title_snippet: str) -> str:
    """Mark a loop as done by fuzzy title/who match (same as voice intent).
    Returns the matched title or error.
    """
    from .voice import state as voice_state  # reuse voice state helpers
    # Note: voice state expects VoiceConfig, but we can adapt or duplicate minimal
    # For simplicity here, direct DB update (mirrors voice/state.py)
    title = _mark_done_direct(CFG, title_snippet)
    if title:
        return f"Marked as done: {title}"
    return "No matching open loop found."

def _mark_done_direct(cfg: Config, phrase: str) -> str | None:
    """Minimal direct DB fuzzy mark (avoid full voice import for MCP)."""
    db = None
    try:
        import sqlite3
        from datetime import datetime, timezone
        db_path = cfg.path("db")
        if not db_path.exists():
            return None
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS loops (id INTEGER PRIMARY KEY, key TEXT UNIQUE, title TEXT, who TEXT, status TEXT, last_action_at TEXT)")
        rows = db.execute("SELECT * FROM loops WHERE status IN ('open','waiting')").fetchall()
        words = [w for w in phrase.lower().split() if len(w) > 2]
        best, best_score = None, 0
        for r in rows:
            hay = f"{r['title']} {r['who'] or ''}".lower()
            score = sum(1 for w in words if w in hay)
            if score > best_score:
                best, best_score = r, score
        if best and best_score > 0:
            now = datetime.now(timezone.utc).astimezone().isoformat()
            db.execute("UPDATE loops SET status='done', last_action_at=? WHERE id=?", (now, best["id"]))
            db.commit()
            return best["title"]
        return None
    except Exception:
        return None
    finally:
        if db:
            db.close()

if __name__ == "__main__":
    # Run as stdio MCP server (for Grok TUI or other clients)
    mcp.run()
