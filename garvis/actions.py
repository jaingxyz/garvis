"""Cleanup actions (soft-delete) with dry-run + safety guards."""
from __future__ import annotations

import re
from typing import Any

from .config import Config
from .gather import Item, notification_match
from .guards import minutes_old, otp_is_deletable, protected_reason
from .mcp_client import Tools

DELETABLE = {"PROMOTION", "UPDATE", "CONCLUDED"}
# A WhatsApp "delete" clears a WHOLE conversation (delete-for-me), which is much heavier than
# trashing a single email — so only marketing/broadcast PROMOTION chats are ever eligible.
WHATSAPP_DELETABLE = {"PROMOTION"}
# An SMS "delete" also clears a whole conversation, but the Google Messages server moves it to
# the Messages Trash (recoverable). Which threads qualify is decided by sms_cleanup_reason.
SMS_DELETABLE = DELETABLE


def _expired_otp(it: Item, cfg: Config) -> bool:
    """An expired one-time code is noise no matter how the model hedged (UNSURE)."""
    return it.label in ("UNSURE", "UPDATE") and otp_is_deletable(it, cfg)


def _older_than(it: Item, days: float) -> bool:
    """Whether the thread's latest message is older than `days`.

    Texts carry no timestamp, so age comes from Item.first_seen — when Garvis first saw
    this exact snippet. A new message resets it, so this really means "no new message in
    N days".
    """
    age = minutes_old(it)
    return age is not None and age > days * 24 * 60


def _stale(it: Item, cfg: Config) -> bool:
    """The grace period ACTIONABLE / PERSONAL notifications get before they count as
    stale noise."""
    return _older_than(it, float(cfg.raw.get("stale_notification_days", 7)))


def _read_and_stale(it: Item, cfg: Config) -> bool:
    """A notification the owner has already read, with no new message in
    sms_read_stale_days. Unlike the rules above this does not care whether the owner once
    replied: an old, read alert thread is spent either way. Never true while the thread
    has unread messages, or when the read state is unknown."""
    return it.unread is False and _older_than(it, float(cfg.raw.get("sms_read_stale_days", 3)))


def _completed_notification(it: Item, cfg: Config) -> str | None:
    """Matches sms_completed_patterns: a delivery/service alert whose job is done
    ("delivered", "repair is complete"). Patterns are written to miss in-flight updates
    such as "out for delivery"."""
    for pat in cfg.raw.get("sms_completed_patterns", []) or []:
        try:
            if re.search(str(pat), it.snippet or "", re.I):
                return str(pat)
        except re.error:
            print(f"[garvis] bad sms_completed_patterns entry {pat!r}; skipped")
    return None


def _is_personal_contact(it: Item, cfg: Config) -> bool:
    """A name the owner listed as a real person — never auto-trashed, whatever the model
    decides. Matched as a case-insensitive substring so "Alex Example" also covers the
    group thread "Realtor, Alex Example"."""
    name = (it.subject or it.id or "").strip().lower()
    if not name:
        return False
    return any(str(p).strip().lower() in name
               for p in cfg.raw.get("sms_personal_contacts", []) or [] if str(p).strip())


def _named_stale_reason(it: Item, cfg: Config) -> str | None:
    """A saved-contact thread that is old AND the model judged non-personal.

    This is the only path by which a named thread is trashed without the owner listing it.
    PERSONAL / ACTIONABLE / WAITING / UNSURE — and anything the model never labelled — are
    kept, so a friend's thread stays even when it is ancient.
    """
    days = float(cfg.raw.get("sms_named_stale_days", 30))
    if not _older_than(it, days):
        return None
    if it.label in ("PROMOTION", "UPDATE", "CONCLUDED"):
        return f"Non-personal thread ({it.label.title()}, >{days:g}d)"
    # The model is unreliable at telling a warm vendor ("Hi Gaurav, thanks so much!") from
    # a friend, so an old named thread that is not on sms_personal_contacts is treated as
    # spent regardless of label. That list — not the model — is what protects people.
    if cfg.raw.get("sms_trash_unlisted_named", False):
        return f"Unlisted contact, silent >{days:g}d"
    return None


def sms_label_free_reason(it: Item, cfg: Config) -> str | None:
    """The cleanup reasons that need neither an LLM label nor the reply check.

    run.py uses this to skip both for a thread that is already condemned — which keeps a
    large scan affordable and avoids opening (and so marking read) threads pointlessly.
    """
    if it.source != "messages" or not cfg.raw.get("allow_sms_delete", False):
        return None
    if _is_personal_contact(it, cfg) or notification_match(it, cfg) is None:
        return None
    done = _completed_notification(it, cfg)
    if done:
        return f"Completed delivery/service alert ({done})"
    if _read_and_stale(it, cfg):
        days = cfg.raw.get("sms_read_stale_days", 3)
        return f"Read notification (no new message in >{days}d)"
    # An alert nobody opened for a month is junk too — being unread does not make a
    # months-old automated notification worth keeping.
    any_days = float(cfg.raw.get("sms_stale_any_days", 30))
    if _older_than(it, any_days):
        return f"Stale notification (no new message in >{any_days:g}d)"
    return None


def sms_cleanup_reason(it: Item, cfg: Config, *, assume_never_replied: bool = False) -> str | None:
    """Why a text thread is eligible for cleanup, or None if it must be kept.

    Everything here requires `allow_sms_delete` and an automated sender: an unknown
    number, or a name/pattern the owner listed as a notification source. A thread with a
    saved contact name is a person and is never touched. Fresh one-time codes are still
    held back by protected_reason.

    Two rules apply even to a thread the owner once replied in, because the thread is
    spent either way: a completed delivery/service alert, and a read thread with no new
    message in sms_read_stale_days. The label-based rules below still require that the
    owner never replied.

    With assume_never_replied=True the reply check is skipped — used to decide whether a
    thread is even worth opening to look for a reply.
    """
    if it.source != "messages" or not cfg.raw.get("allow_sms_delete", False):
        return None
    if _is_personal_contact(it, cfg):
        return None
    # Only automated senders are ever trashed. A thread with a saved contact name is a
    # person (unless the owner explicitly listed it), and the model's label for a person's
    # thread can flip run to run — trashing their whole conversation is not worth it.
    why = notification_match(it, cfg)
    if why is None:
        # A saved contact: only the old + judged-non-personal rule can touch it.
        return _named_stale_reason(it, cfg)
    if _expired_otp(it, cfg):
        return "Expired one-time code"
    label_free = sms_label_free_reason(it, cfg)
    if label_free:
        return label_free
    # A thread the owner has replied in is a conversation, not a notification. Unknown
    # state (thread-state check failed, not run, or inconclusive) is treated the same: keep.
    if not assume_never_replied and it.owner_replied is not False:
        return None
    if why != "unknown number":
        # Owner-listed notification sender/pattern (e.g. voicemail alerts): goes regardless
        # of label, since the owner said this thread is machine noise.
        return f"Notification ({why[len('notification '):]}, never replied)"
    if it.label in SMS_DELETABLE:
        return it.label.title()
    if it.label == "UNSURE":
        return "Unknown number (never replied)"
    # ACTIONABLE / PERSONAL / WAITING: give the owner stale_notification_days to act first.
    if _stale(it, cfg):
        days = cfg.raw.get("stale_notification_days", 7)
        return f"Stale notification (unknown number, no reply, >{days}d)"
    return None


def _is_deletable(it: Item, cfg: Config) -> bool:
    """Whether an item's label makes it eligible for cleanup, per source-specific rules."""
    if it.source == "whatsapp":
        # Extra opt-in beyond dry_run: conversation deletion stays off unless explicitly enabled.
        if not cfg.raw.get("allow_whatsapp_delete", False):
            return False
        return it.label in WHATSAPP_DELETABLE
    if it.source == "messages":
        return sms_cleanup_reason(it, cfg) is not None
    return it.label in DELETABLE or _expired_otp(it, cfg)


def _cleanup_reason(it: Item, cfg: Config) -> str:
    if it.source == "messages":
        return sms_cleanup_reason(it, cfg) or it.label.title()
    if _expired_otp(it, cfg):
        return "Expired one-time code"
    return it.label.title()


def _ensure_ok(result: Any) -> None:
    """Raise if an MCP tool reported failure in-band.

    langchain-mcp-adapters (handle_tool_errors=True by default) turns a server's isError
    result into a plain "Error: ..." text block instead of raising, and the local servers
    return {"ok": false, ...} / {"error": ...} shapes for soft failures. A deletion must
    never be recorded as performed on such a result.
    """
    if isinstance(result, str) and result.lstrip().lower().startswith("error"):
        raise RuntimeError(result.strip()[:300])
    if isinstance(result, dict):
        if result.get("isError") or result.get("ok") is False:
            raise RuntimeError(str(result)[:300])
        if result.get("error"):
            raise RuntimeError(str(result["error"])[:300])


async def cleanup(tools: Tools, cfg: Config, items: list[Item]) -> list[dict]:
    """Soft-delete deletable items. Returns a log of (intended or performed) deletions."""
    log = []
    for it in items:
        if not _is_deletable(it, cfg) or protected_reason(it, cfg):
            continue
        entry = {
            "account": it.source, "sender": it.sender, "subject": it.subject,
            "reason": _cleanup_reason(it, cfg), "id": it.id,
            "performed": False, "dry_run": cfg.dry_run,
        }
        if cfg.dry_run:
            log.append(entry)
            continue
        try:
            if it.source == "gmail":
                res = await tools.call("gmail_delete", messageId=it.id)   # soft by default
            elif it.source == "outlook":
                res = await tools.call("personal_email_delete", messageId=it.id)
            elif it.source == "whatsapp":
                # it.id is the chat jid; delete-for-me of the whole conversation. The MCP tool
                # requires confirm=True to actually act (otherwise it only previews).
                res = await tools.call("whatsapp_delete_conversation", chat=it.id, confirm=True)
            elif it.source == "messages":
                # it.id is the exact conversation name; the MCP tool moves the whole conversation
                # to the Messages Trash and refuses if the name is ambiguous.
                res = await tools.call("delete_conversation", name=it.id)
            else:
                # No delete path for this source — never report a phantom deletion.
                entry["error"] = f"no cleanup action for source '{it.source}'"
                log.append(entry)
                continue
            _ensure_ok(res)
            entry["performed"] = True
        except Exception as e:
            entry["error"] = str(e)
        log.append(entry)
    return log
