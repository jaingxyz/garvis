"""Gather items from the MCP servers and normalize them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC

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
    # When the source gives no timestamp (SMS, WhatsApp), this is the ISO time Garvis first
    # saw this exact snippet in this thread (set by Store.stamp_first_seen); guards use it as
    # the item's age so the OTP grace window can expire instead of protecting codes forever.
    first_seen: str = ""
    thread_id: str = ""
    labels: list[str] = field(default_factory=list)
    owner_replied_last: bool | None = None   # set by thread-state check
    owner_replied: bool | None = None        # SMS: has the owner ever sent in this thread
    last_msg_from: str = ""                  # who sent the latest message in the thread
    last_msg_text: str = ""                  # snippet of that latest message
    has_attachments: bool = False
    # filled in by classify:
    label: str = ""
    reason: str = ""
    summary: str = ""
    task: str = ""
    deadline: str = ""


async def gather_gmail(tools: Tools, cfg: Config, *, lookback_minutes: int | None = None) -> list[Item]:
    if lookback_minutes:
        # delta / incremental mode for quick back-and-forth
        query = f"newer_than:{lookback_minutes}m"
        limit = min(cfg.scan_limits.get("gmail", 60), 30)
    else:
        days = cfg.window_days
        limit = cfg.scan_limits.get("gmail", 60)
        query = f"newer_than:{days}d"
    res = await tools.call("gmail_search", query=query, limit=limit)
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


async def gather_outlook(tools: Tools, cfg: Config, *, lookback_minutes: int | None = None) -> list[Item]:
    limit = min(cfg.scan_limits.get("outlook", 60), 30) if lookback_minutes else cfg.scan_limits.get("outlook", 60)
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
    except Exception as e:
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


def is_unnamed_sender(it: Item) -> bool:
    """True for a text thread whose name is a bare phone number or short code (no letters),
    i.e. not a saved contact. An empty name is *not* unnamed: it means the list scrape
    missed the name element, and acting on name="" could hit the wrong thread."""
    name = it.subject or it.id or ""
    return it.source == "messages" and bool(name.strip()) and not re.search(r"[^\W\d_]", name)


def notification_match(it: Item, cfg: Config) -> str | None:
    """Why a text thread counts as an automated notification sender, or None.

    Unnamed senders (numbers, short codes) always do. Named threads only do when the
    owner lists them under `sms_notification_senders` (exact name, case-insensitive) or
    the latest snippet matches one of `sms_notification_patterns` (regex, case-insensitive)
    — e.g. carrier voicemail alerts that land in a thread named after the owner's own
    number.
    """
    if it.source != "messages":
        return None
    if is_unnamed_sender(it):
        return "unknown number"
    name = (it.subject or it.id or "").strip().lower()
    for s in cfg.raw.get("sms_notification_senders", []) or []:
        if str(s).strip().lower() == name:
            return f"notification sender ({s})"
    for pat in cfg.raw.get("sms_notification_patterns", []) or []:
        try:
            if re.search(str(pat), it.snippet or "", re.I):
                return f"notification pattern ({pat})"
        except re.error:
            print(f"[garvis] bad sms_notification_patterns entry {pat!r}; skipped")
    return None


SMS_THREAD_READ_LIMIT = 100


async def check_sms_thread_state(tools: Tools, it: Item) -> None:
    """Fill owner_replied / owner_replied_last for a text thread from its message directions.

    read_conversation returns [{from: "me"|"them", text}] oldest-first; it carries no
    timestamps. On any failure the fields stay None, which the cleanup treats as "unknown,
    keep" — never as "not replied". The same applies when the read comes back empty (the
    scraper's selectors may simply have matched nothing) and when a full page of messages
    shows no owner reply (an older reply could sit beyond the page).
    """
    try:
        res = await tools.call("read_conversation", name=it.id, limit=SMS_THREAD_READ_LIMIT)
    except Exception as e:
        print(f"[garvis] sms thread-state failed for {it.id!r}: {e}")
        return
    msgs = res.get("messages", res) if isinstance(res, dict) else res
    if not isinstance(msgs, list) or not msgs or not all(isinstance(m, dict) for m in msgs):
        return
    replied = any(m.get("from") == "me" for m in msgs)
    if replied or len(msgs) < SMS_THREAD_READ_LIMIT:
        it.owner_replied = replied
    last = msgs[-1]
    it.owner_replied_last = last.get("from") == "me"
    it.last_msg_from = "owner" if it.owner_replied_last else it.subject
    it.last_msg_text = str(last.get("text", ""))[:300]


async def gather_whatsapp(tools: Tools, cfg: Config, *, lookback_minutes: int | None = None) -> list[Item]:
    """WhatsApp chats via the Baileys-based whatsapp-mcp (tools prefixed whatsapp_).

    The server connects lazily on this call and reads its local store; history fills in
    over time, so an early run may see fewer chats than a long-lived daemon would.
    """
    limit = min(cfg.scan_limits.get("whatsapp", 20), 15) if lookback_minutes else cfg.scan_limits.get("whatsapp", 20)
    try:
        res = await tools.call("whatsapp_list_conversations", limit=limit)
    except Exception as e:
        print(f"[garvis] whatsapp unavailable: {e}")
        return []
    if isinstance(res, dict):
        convs = res.get("conversations", [])
    elif isinstance(res, list):
        convs = res
    else:
        print(f"[garvis] whatsapp not ready: {str(res)[:120]}")
        return []
    items = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        name = c.get("name", "")
        items.append(Item(
            source="whatsapp", id=c.get("jid") or name, subject=name,
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
    from datetime import datetime

    from .dates import _parse_date

    floor = datetime.min.replace(tzinfo=UTC)

    def when(it: Item) -> datetime:
        dt = _parse_date(it.date)
        if dt is None:
            return floor
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

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
    except Exception as e:
        print(f"[garvis] thread-state check failed for {item.subject!r}: {e}")
