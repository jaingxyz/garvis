"""Cleanup actions (soft-delete) with dry-run + safety guards."""
from __future__ import annotations

from .config import Config
from .gather import Item, is_unnamed_sender
from .guards import minutes_old, otp_is_deletable, protected_reason
from .mcp_client import Tools

DELETABLE = {"PROMOTION", "UPDATE", "CONCLUDED"}
# A WhatsApp "delete" clears a WHOLE conversation (delete-for-me), which is much heavier than
# trashing a single email — so only marketing/broadcast PROMOTION chats are ever eligible.
WHATSAPP_DELETABLE = {"PROMOTION"}
# An SMS "delete" also clears a whole conversation, but the Google Messages server moves it to
# the Messages Trash (recoverable), so it follows the same label policy as email — behind its
# own opt-in flag because a thread with a real contact may carry history worth keeping.
SMS_DELETABLE = DELETABLE


def _expired_otp(it: Item, cfg: Config) -> bool:
    """An expired one-time code is noise no matter how the model hedged (UNSURE)."""
    return it.label in ("UNSURE", "UPDATE") and otp_is_deletable(it, cfg)


def _stale_notification(it: Item, cfg: Config) -> bool:
    """An unknown-number text the owner never answered, older than stale_notification_days.

    Covers the ACTIONABLE / PERSONAL labels the model sometimes gives payment alerts and
    app notifications: they get a grace period to be acted on, then count as stale noise.
    Age comes from Item.first_seen (when Garvis first saw the message) since texts carry
    no timestamp.
    """
    if not is_unnamed_sender(it) or it.owner_replied is not False:
        return False
    days = float(cfg.raw.get("stale_notification_days", 7))
    age = minutes_old(it)
    return age is not None and age > days * 24 * 60


def _sms_deletable(it: Item, cfg: Config) -> bool:
    # Extra opt-in beyond dry_run: SMS conversation cleanup stays off unless explicitly enabled.
    if not cfg.raw.get("allow_sms_delete", False):
        return False
    # Only unnamed senders (phone numbers, short codes) are ever trashed. A thread with a
    # saved contact name is a person, and the model's label for a person's thread can flip
    # run to run — trashing their whole conversation is not worth it.
    if not is_unnamed_sender(it):
        return False
    # A thread the owner has replied in is a conversation, not a notification. Unknown
    # state (thread-state check failed) is treated the same way: keep.
    if it.owner_replied is not False:
        return False
    # Unknown number, never answered: notifications/promos/anything the model could not
    # place go now (fresh one-time codes are still held back by protected_reason);
    # ACTIONABLE / PERSONAL wait out stale_notification_days first.
    if it.label in SMS_DELETABLE or it.label == "UNSURE" or _expired_otp(it, cfg):
        return True
    return _stale_notification(it, cfg)


def _is_deletable(it: Item, cfg: Config) -> bool:
    """Whether an item's label makes it eligible for cleanup, per source-specific rules."""
    if it.source == "whatsapp":
        # Extra opt-in beyond dry_run: conversation deletion stays off unless explicitly enabled.
        if not cfg.raw.get("allow_whatsapp_delete", False):
            return False
        return it.label in WHATSAPP_DELETABLE
    if it.source == "messages":
        return _sms_deletable(it, cfg)
    return it.label in DELETABLE or _expired_otp(it, cfg)


def _cleanup_reason(it: Item, cfg: Config) -> str:
    if _expired_otp(it, cfg):
        return "Expired one-time code"
    if it.source == "messages" and it.label not in SMS_DELETABLE:
        if _stale_notification(it, cfg):
            return f"Stale notification (unknown number, no reply, >{cfg.raw.get('stale_notification_days', 7)}d)"
        return "Unknown number (never replied)"
    return it.label.title()


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
                await tools.call("gmail_delete", messageId=it.id)   # soft by default
            elif it.source == "outlook":
                await tools.call("personal_email_delete", messageId=it.id)
            elif it.source == "whatsapp":
                # it.id is the chat jid; delete-for-me of the whole conversation. The MCP tool
                # requires confirm=True to actually act (otherwise it only previews).
                await tools.call("whatsapp_delete_conversation", chat=it.id, confirm=True)
            elif it.source == "messages":
                # it.id is the exact conversation name; the MCP tool moves the whole conversation
                # to the Messages Trash and refuses if the name is ambiguous.
                await tools.call("delete_conversation", name=it.id)
            else:
                # No delete path for this source — never report a phantom deletion.
                entry["error"] = f"no cleanup action for source '{it.source}'"
                log.append(entry)
                continue
            entry["performed"] = True
        except Exception as e:
            entry["error"] = str(e)
        log.append(entry)
    return log
