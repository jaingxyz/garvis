"""Persistent local storage for Garvis: run history, deletion audit, seen items.

Plain SQLite (stdlib). The audit log means every soft-delete is recorded with its
message id, so you can review or recover even after the digest is gone.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  mode        TEXT,            -- 'live' or 'dry-run'
  scanned     INTEGER,
  threads     INTEGER,
  deleted     INTEGER,
  flagged     INTEGER
);
CREATE TABLE IF NOT EXISTS actions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER,
  ts         TEXT,
  account    TEXT,
  sender     TEXT,
  subject    TEXT,
  message_id TEXT,
  decision   TEXT,             -- Promotion / Update / Concluded
  performed  INTEGER,          -- 1 if actually deleted, 0 if dry-run/error
  dry_run    INTEGER
);
CREATE TABLE IF NOT EXISTS seen (
  message_id TEXT PRIMARY KEY,
  account    TEXT,
  subject    TEXT,
  label      TEXT,
  first_seen TEXT,
  last_run_id INTEGER
);
-- Durable state ("memory"): people/orgs and open loops, so Garvis isn't re-derived
-- from scratch each run and a single bad sweep can't silently reopen a settled thread.
CREATE TABLE IF NOT EXISTS entities (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  handle     TEXT UNIQUE,        -- email / phone / normalized name / 'self'
  name       TEXT,
  kind       TEXT,               -- person | org
  role       TEXT,               -- "family", "relocation", "school", ...
  aliases    TEXT,               -- comma list: "Alex, spouse"
  vip        INTEGER DEFAULT 0,
  notes      TEXT,               -- free-form context the LLM reads
  created_at TEXT, updated_at TEXT
);
-- The memory graph's edges. subject/object are entity handles ('self' = the user);
-- object_text holds a literal for facts (relocating_to -> "California").
CREATE TABLE IF NOT EXISTS relations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  subject       TEXT,            -- entity handle
  predicate     TEXT,            -- spouse_of | parent_of | relocating_to | works_at | fact | note
  object_handle TEXT,            -- entity handle, if the edge points at another node
  object_text   TEXT,            -- literal value, if it's a fact/note
  created_at TEXT, updated_at TEXT,
  UNIQUE(subject, predicate, object_handle, object_text)
);
CREATE TABLE IF NOT EXISTS loops (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  key            TEXT UNIQUE,    -- stable thread key (source + thread_id or norm subject)
  source         TEXT,
  thread_id      TEXT,
  title          TEXT,           -- short description (subject)
  who            TEXT,           -- counterparty
  status         TEXT,           -- open | waiting | done | snoozed
  blocks_id      INTEGER,        -- poor-man's graph edge: this loop blocks loop <id>
  last_inbound_at TEXT,          -- ISO time of latest message FROM the other party
  last_action_at  TEXT,          -- ISO time status last changed (owner acted / concluded)
  snooze_until    TEXT,
  created_at TEXT, updated_at TEXT,
  run_id     INTEGER
);
"""

# label -> loop status. PERSONAL/UNSURE/PROMOTION/UPDATE are not tracked as loops.
_LABEL_STATUS = {"ACTIONABLE": "open", "WAITING": "waiting", "CONCLUDED": "done"}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a table first shipped (older DBs)."""
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(entities)")}
        if "aliases" not in cols:
            self.db.execute("ALTER TABLE entities ADD COLUMN aliases TEXT")
            self.db.commit()

    # --- run lifecycle ---
    def start_run(self, mode: str) -> int:
        cur = self.db.execute(
            "INSERT INTO runs (started_at, mode) VALUES (?, ?)", (_now(), mode))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, *, scanned: int, threads: int,
                   deleted: int, flagged: int) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at=?, scanned=?, threads=?, deleted=?, flagged=? "
            "WHERE id=?",
            (_now(), scanned, threads, deleted, flagged, run_id))
        self.db.commit()

    # --- audit + seen ---
    def record_actions(self, run_id: int, log: list[dict]) -> None:
        self.db.executemany(
            "INSERT INTO actions (run_id, ts, account, sender, subject, message_id, "
            "decision, performed, dry_run) VALUES (?,?,?,?,?,?,?,?,?)",
            [(run_id, _now(), e["account"], e["sender"], e["subject"], e["id"],
              e["reason"], int(e.get("performed", False)), int(e.get("dry_run", False)))
             for e in log])
        self.db.commit()

    def mark_seen(self, run_id: int, items: list) -> None:
        self.db.executemany(
            "INSERT INTO seen (message_id, account, subject, label, first_seen, last_run_id) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(message_id) DO UPDATE SET label=excluded.label, "
            "last_run_id=excluded.last_run_id",
            [(it.id, it.source, it.subject, it.label, _now(), run_id) for it in items])
        self.db.commit()

    def already_deleted(self, message_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM actions WHERE message_id=? AND performed=1 LIMIT 1",
            (message_id,)).fetchone()
        return row is not None

    # --- durable state: entities + loops ---
    def seed_entities(self, vip_handles: list[str]) -> None:
        """Make sure VIP senders exist as entities (idempotent)."""
        for h in vip_handles:
            self.db.execute(
                "INSERT INTO entities (handle, kind, vip, created_at, updated_at) "
                "VALUES (?,?,1,?,?) ON CONFLICT(handle) DO UPDATE SET vip=1, updated_at=?",
                (h.lower(), "person", _now(), _now(), _now()))
        self.db.commit()

    # --- memory graph: entities + relations ---
    def upsert_entity(self, handle: str, *, name: str = "", kind: str = "person",
                      role: str = "", aliases: str = "", vip: bool = False,
                      notes: str = "") -> None:
        h = handle.lower().strip()
        self.db.execute(
            "INSERT INTO entities (handle, name, kind, role, aliases, vip, notes, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(handle) DO UPDATE SET "
            "name=COALESCE(NULLIF(excluded.name,''), entities.name), "
            "kind=excluded.kind, "
            "role=COALESCE(NULLIF(excluded.role,''), entities.role), "
            "aliases=COALESCE(NULLIF(excluded.aliases,''), entities.aliases), "
            "vip=excluded.vip, "
            "notes=COALESCE(NULLIF(excluded.notes,''), entities.notes), "
            "updated_at=excluded.updated_at",
            (h, name, kind, role, aliases, int(vip), notes, _now(), _now()))
        self.db.commit()

    def upsert_relation(self, subject: str, predicate: str, *,
                        object_handle: str = "", object_text: str = "") -> None:
        self.db.execute(
            "INSERT INTO relations (subject, predicate, object_handle, object_text, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(subject, predicate, object_handle, object_text) "
            "DO UPDATE SET updated_at=excluded.updated_at",
            (subject.lower(), predicate, (object_handle or "").lower(), object_text,
             _now(), _now()))
        self.db.commit()

    def sync_profile(self, profile: dict) -> None:
        """Idempotently load config/profile.yaml into the graph (self + people + facts)."""
        if not profile:
            return
        me = profile.get("self", {}) or {}
        self.upsert_entity("self", name=me.get("name", "You"), kind="person",
                           role="self", vip=True,
                           aliases=", ".join(me.get("handles", []) or []))
        for fact in me.get("facts", []) or []:
            self.upsert_relation("self", "fact", object_text=str(fact))
        _REL = {"spouse": "spouse_of", "wife": "spouse_of", "husband": "spouse_of",
                "child": "parent_of", "son": "parent_of", "daughter": "parent_of"}
        for p in profile.get("people", []) or []:
            handle = (p.get("handle") or p.get("name", "")).lower()
            if not handle:
                continue
            self.upsert_entity(
                handle, name=p.get("name", ""), kind=p.get("kind", "person"),
                role=p.get("role", ""), vip=bool(p.get("vip")),
                aliases=", ".join(p.get("aliases", []) or []),
                notes=p.get("context", ""))
            rel = p.get("relation")
            if rel:
                self.upsert_relation("self", _REL.get(rel, rel), object_handle=handle)
            if p.get("org"):
                self.upsert_relation(handle, "affiliated_with", object_text=p["org"])

    def profile_context(self) -> str:
        """Render the graph into a compact 'who's who + your situation' block for prompts."""
        ents = {r["handle"]: r for r in self.db.execute("SELECT * FROM entities")}
        rels = self.db.execute("SELECT * FROM relations").fetchall()
        if not ents and not rels:
            return ""
        me = ents.get("self")
        lines: list[str] = []
        facts = [r["object_text"] for r in rels
                 if r["subject"] == "self" and r["predicate"] in ("fact", "relocating_to")]
        if me or facts:
            who = (me["name"] if me else "You")
            tail = f" — {'; '.join(facts)}" if facts else ""
            lines.append(f"- You ({who}){tail}")
        # edges from self to people, to describe relationships in plain words
        rel_label = {"spouse_of": "your spouse", "parent_of": "your child",
                     "works_at": "your employer"}
        person_rel: dict[str, str] = {}
        for r in rels:
            if r["subject"] == "self" and r["object_handle"]:
                person_rel[r["object_handle"]] = rel_label.get(r["predicate"], r["predicate"])
        for h, e in ents.items():
            if h == "self":
                continue
            bits = [e["name"] or h]
            if e["handle"] and e["handle"] != (e["name"] or "").lower():
                bits.append(f"<{e['handle']}>")
            tags = [t for t in (person_rel.get(h), e["role"]) if t]
            if e["vip"]:
                tags.append("VIP")
            head = f"- {' '.join(bits)}" + (f" — {', '.join(tags)}" if tags else "")
            if e["notes"]:
                head += f". {e['notes']}"
            lines.append(head)
        return ("WHO'S WHO & YOUR SITUATION (durable memory — use it to judge importance, "
                "relationships, and what a message really means):\n" + "\n".join(lines))

    def sync_loops(self, run_id: int, items: list) -> None:
        """Upsert one loop per tracked thread, with a STICKY-DONE rule: a sweep may never
        flip a done/snoozed loop back to open/waiting unless a genuinely NEWER inbound
        message has arrived since it was settled. This is what stops a single mis-judged
        run (e.g. a flaky Outlook thread-state search) from reopening a closed thread."""
        from .gather import _norm_subject
        from .guards import _parse_date

        def newer(a: str | None, b: str | None) -> bool:
            da, db = _parse_date(a or ""), _parse_date(b or "")
            if da is None:
                return False
            if db is None:
                return True
            da = da if da.tzinfo else da.replace(tzinfo=timezone.utc)
            db = db if db.tzinfo else db.replace(tzinfo=timezone.utc)
            return da > db

        for it in items:
            status_new = _LABEL_STATUS.get(it.label)
            if status_new is None:
                continue  # not a tracked loop (PERSONAL/UNSURE/PROMOTION/UPDATE)
            key = f"{it.source}:{it.thread_id or 'subj:' + _norm_subject(it.subject)}"
            # latest INBOUND time = this thread's latest message, only if it wasn't us.
            inbound_at = it.date if it.owner_replied_last is False else None
            now = _now()
            row = self.db.execute("SELECT * FROM loops WHERE key=?", (key,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO loops (key, source, thread_id, title, who, status, "
                    "last_inbound_at, last_action_at, created_at, updated_at, run_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (key, it.source, it.thread_id, it.subject, it.sender, status_new,
                     inbound_at, now, now, now, run_id))
                continue
            status = status_new
            last_action = now
            # 'done' is settled; 'snoozed' is settled only until snooze_until passes.
            snooze_active = (row["status"] == "snoozed"
                             and (row["snooze_until"] or "") > now)
            settled = row["status"] == "done" or snooze_active
            if settled and status_new in ("open", "waiting"):
                # only reopen on a genuinely newer inbound than when we settled it
                if inbound_at and newer(inbound_at, row["last_action_at"]):
                    status, last_action = status_new, now
                else:
                    status, last_action = row["status"], row["last_action_at"]
            elif status_new == row["status"]:
                last_action = row["last_action_at"]  # unchanged; preserve original timestamp
            self.db.execute(
                "UPDATE loops SET source=?, thread_id=?, title=?, who=?, status=?, "
                "last_inbound_at=COALESCE(?, last_inbound_at), last_action_at=?, "
                "updated_at=?, run_id=? WHERE key=?",
                (it.source, it.thread_id, it.subject, it.sender, status,
                 inbound_at, last_action, now, run_id, key))
        self.db.commit()

    def open_loops(self) -> list[sqlite3.Row]:
        # open/waiting, plus snoozed loops whose snooze window has elapsed (due again).
        now = _now()
        return self.db.execute(
            "SELECT * FROM loops WHERE status IN ('open','waiting') "
            "OR (status='snoozed' AND (snooze_until IS NULL OR snooze_until <= ?)) "
            "ORDER BY status, updated_at DESC", (now,)).fetchall()

    # --- read for the history/monitor view ---
    def recent_runs(self, n: int = 10) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()

    def recent_actions(self, n: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (n,)).fetchall()
