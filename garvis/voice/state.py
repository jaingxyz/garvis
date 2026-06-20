"""Read + mutate Garvis's durable loop state from the voice layer.

Uses stdlib sqlite3 against the same state/garvis.db the sweep writes (the voice venv
deliberately doesn't import the langchain `garvis` package). The sweep owns the schema,
but we ensure the tables exist so voice works even before the next sweep runs.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import VoiceConfig

_ENSURE = """
CREATE TABLE IF NOT EXISTS loops (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, source TEXT, thread_id TEXT,
  title TEXT, who TEXT, status TEXT, blocks_id INTEGER, last_inbound_at TEXT,
  last_action_at TEXT, snooze_until TEXT, created_at TEXT, updated_at TEXT, run_id INTEGER
);
"""


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat()


def _connect(cfg: VoiceConfig) -> sqlite3.Connection | None:
    if not Path(cfg.db_path).exists():
        return None
    db = sqlite3.connect(str(cfg.db_path))
    db.row_factory = sqlite3.Row
    db.executescript(_ENSURE)
    return db


def open_loops(cfg: VoiceConfig) -> list[sqlite3.Row]:
    db = _connect(cfg)
    if db is None:
        return []
    rows = db.execute(
        "SELECT * FROM loops WHERE status IN ('open','waiting') "
        "OR (status='snoozed' AND (snooze_until IS NULL OR snooze_until <= ?)) "
        "ORDER BY status, updated_at DESC", (_now(),)).fetchall()
    db.close()
    return rows


def loops_context(cfg: VoiceConfig) -> str:
    """A compact, authoritative snapshot of live open loops for the answer prompt."""
    rows = open_loops(cfg)
    if not rows:
        return ""
    lines = [f"- [{r['status']}] {r['title']} (with {r['who'] or '?'})" for r in rows]
    header = ("LIVE OPEN LOOPS (authoritative — trust these over the briefing text below; "
              "anything not listed here is resolved/done):")
    return header + "\n" + "\n".join(lines)


def _match(cfg: VoiceConfig, phrase: str) -> sqlite3.Row | None:
    """Fuzzy-match a spoken phrase to an open loop by words in its title/counterparty."""
    rows = open_loops(cfg)
    words = [w for w in phrase.lower().split() if len(w) > 2]
    best, best_score = None, 0
    for r in rows:
        hay = f"{r['title']} {r['who']}".lower()
        score = sum(1 for w in words if w in hay)
        if score > best_score:
            best, best_score = r, score
    return best if best_score > 0 else None


def mark_done(cfg: VoiceConfig, phrase: str) -> str | None:
    db = _connect(cfg)
    if db is None:
        return None
    row = _match(cfg, phrase)
    if row is None:
        db.close()
        return None
    db.execute("UPDATE loops SET status='done', last_action_at=?, updated_at=? WHERE id=?",
               (_now(), _now(), row["id"]))
    db.commit()
    db.close()
    return row["title"]


def snooze(cfg: VoiceConfig, phrase: str, days: int = 1) -> str | None:
    db = _connect(cfg)
    if db is None:
        return None
    row = _match(cfg, phrase)
    if row is None:
        db.close()
        return None
    until = (datetime.now(UTC).astimezone() + timedelta(days=days)).isoformat()
    db.execute("UPDATE loops SET status='snoozed', snooze_until=?, last_action_at=?, "
               "updated_at=? WHERE id=?", (until, _now(), _now(), row["id"]))
    db.commit()
    db.close()
    return row["title"]
