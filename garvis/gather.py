"""Gather items from the MCP servers and normalize them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config
from .mcp_client import Tools


@dataclass
class Item:
    source: str          # gmail | outlook | messages
    id: str
    subject: str
    sender: str
    date: str
    snippet: str
    thread_id: str = ""
    labels: list[str] = field(default_factory=list)
    owner_replied_last: bool | None = None   # set by thread-state check
    last_msg_from: str = ""                  # who sent the latest message in the thread
    last_msg_text: str = ""                  # snippet of that latest message
    has_attachments: bool = False
    # filled in by classify:
    label: str = ""
    reason: str = ""
    summary: str = ""
    task: str = ""
    deadline: str = ""


async def gather_gmail(tools: Tools, cfg: Config) -> list[Item]:
    days = cfg.window_days
    limit = cfg.scan_limits.get("gmail", 60)
    res = await tools.call("gmail_search", query=f"newer_than:{days}d", limit=limit)
    items = []
    for m in res.get("messages", []):
        if "SENT" in m.get("labelIds", []):
            continue
        items.append(Item(
            source="gmail", id=m["id"], thread_id=m.get("threadId", ""),
            subject=m.get("subject", ""), sender=m.get("from", ""),
            date=m.get("date", ""), snippet=m.get("snippet", ""),
            labels=m.get("labelIds", []),
            has_attachments=bool(m.get("hasAttachments")),
        ))
    return items


async def gather_outlook(tools: Tools, cfg: Config) -> list[Item]:
    limit = cfg.scan_limits.get("outlook", 60)
    res = await tools.call("personal_email_list_recent", folder="inbox", limit=limit)
    items = []
    for m in res.get("messages", []):
        frm = m.get("from", "")
        if isinstance(frm, dict):
            frm = frm.get("address", "")
        items.append(Item(
            source="outlook", id=m["id"], thread_id=m.get("conversationId", ""),
            subject=m.get("subject", ""), sender=str(frm),
            date=m.get("receivedDateTime", ""), snippet=m.get("preview", ""),
            has_attachments=bool(m.get("hasAttachments")),
        ))
    return items


async def gather_messages(tools: Tools, cfg: Config) -> list[Item]:
    """Best-effort: the Google Messages server uses a browser profile that may be locked."""
    limit = cfg.scan_limits.get("messages", 20)
    try:
        res = await tools.call("list_conversations", limit=limit)
    except Exception as e:  # noqa: BLE001 - surfaced in the digest as "unavailable"
        print(f"[garvis] texts unavailable: {e}")
        return []
    # list_conversations returns a list of {index,name,snippet,unread} dicts; during
    # browser warm-up it may instead return a status string — treat that as "no texts".
    if isinstance(res, dict):
        convs = res.get("conversations", [])
    elif isinstance(res, list):
        convs = res
    else:
        print(f"[garvis] texts not ready: {str(res)[:120]}")
        return []
    items = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        items.append(Item(
            source="messages", id=name, subject=name,
            sender=name, date="", snippet=c.get("snippet", ""),
        ))
    return items


_REPLY_PREFIX = re.compile(r"^\s*(re|fwd?|aw|sv)\s*:\s*", re.IGNORECASE)


def _norm_subject(subject: str) -> str:
    """Strip reply/forward prefixes and collapse whitespace for thread grouping."""
    s = subject or ""
    prev = None
    while prev != s:                       # strip repeated "Re: Fwd: ..." prefixes
        prev = s
        s = _REPLY_PREFIX.sub("", s)
    return " ".join(s.split()).lower()


def dedupe_threads(items: list[Item]) -> list[Item]:
    """Collapse multiple messages of the same thread to its single latest message.

    Uses the thread id when the server provides one (Gmail threadId / Outlook
    conversationId), otherwise falls back to the normalized subject — needed because
    the Outlook MCP's list_recent does not return a conversation id.
    """
    from datetime import datetime, timezone

    from .guards import _parse_date

    floor = datetime.min.replace(tzinfo=timezone.utc)

    def when(it: Item) -> datetime:
        dt = _parse_date(it.date)
        if dt is None:
            return floor
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    best: dict[tuple, Item] = {}
    for it in items:
        key = (it.source, it.thread_id or f"subj:{_norm_subject(it.subject)}")
        if key not in best or when(it) > when(best[key]):
            best[key] = it
    return list(best.values())


async def check_thread_state(tools: Tools, cfg: Config, item: Item) -> None:
    """Set owner_replied_last so WAITING vs ACTIONABLE can be decided correctly.

    Gracefully skips if the MCP doesn't expose the needed thread tools
    (e.g. some personal-gmail-mcp builds lack gmail_get_thread).
    """
    owner_tokens = {cfg.owner_gmail.lower(), cfg.owner_outlook.lower()}
    available = set(tools.names())

    try:
        if item.source == "gmail" and item.thread_id:
            if "gmail_get_thread" in available:
                thread = await tools.call("gmail_get_thread", threadId=item.thread_id)
                msgs = thread.get("messages", []) if isinstance(thread, dict) else []
            else:
                # Fallback: search by thread (many gmail MCPs support threadId: in query)
                res = await tools.call("gmail_search", query=f"threadId:{item.thread_id}", limit=5)
                msgs = res.get("messages", []) if isinstance(res, dict) else []
            if msgs:
                last = msgs[-1]
                item.last_msg_from = str(last.get("from", ""))
                item.last_msg_text = last.get("snippet", "") or last.get("preview", "")
                item.owner_replied_last = any(
                    t in item.last_msg_from.lower() for t in owner_tokens)

        elif item.source == "outlook" and item.subject:
            if "personal_email_search" in available:
                res = await tools.call("personal_email_search", query=item.subject, limit=10)
                msgs = res.get("messages", []) if isinstance(res, dict) else []
                msgs = sorted(msgs, key=lambda m: m.get("receivedDateTime", ""), reverse=True)
                if msgs:
                    last = msgs[0]
                    frm = last.get("from", "")
                    if isinstance(frm, dict):
                        frm = frm.get("address", "")
                    item.last_msg_from = str(frm)
                    item.last_msg_text = last.get("preview", "")
                    item.owner_replied_last = any(
                        t in item.last_msg_from.lower() for t in owner_tokens)
    except Exception as e:  # noqa: BLE001
        print(f"[garvis] thread-state check failed for {item.subject!r}: {e}")
