"""Cleanup actions (soft-delete) with dry-run + safety guards."""
from __future__ import annotations

from .config import Config
from .gather import Item
from .guards import protected_reason
from .mcp_client import Tools

DELETABLE = {"PROMOTION", "UPDATE", "CONCLUDED"}
# A WhatsApp "delete" clears a WHOLE conversation (delete-for-me), which is much heavier than
# trashing a single email — so only marketing/broadcast PROMOTION chats are ever eligible.
WHATSAPP_DELETABLE = {"PROMOTION"}


def _is_deletable(it: Item, cfg: Config) -> bool:
    """Whether an item's label makes it eligible for cleanup, per source-specific rules."""
    if it.source == "whatsapp":
        # Extra opt-in beyond dry_run: conversation deletion stays off unless explicitly enabled.
        if not cfg.raw.get("allow_whatsapp_delete", False):
            return False
        return it.label in WHATSAPP_DELETABLE
    return it.label in DELETABLE


async def cleanup(tools: Tools, cfg: Config, items: list[Item]) -> list[dict]:
    """Soft-delete deletable items. Returns a log of (intended or performed) deletions."""
    log = []
    for it in items:
        if not _is_deletable(it, cfg) or protected_reason(it, cfg):
            continue
        entry = {
            "account": it.source, "sender": it.sender, "subject": it.subject,
            "reason": it.label.title(), "id": it.id,
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
            else:
                # No delete path for this source (e.g. SMS) — never report a phantom deletion.
                entry["error"] = f"no cleanup action for source '{it.source}'"
                log.append(entry)
                continue
            entry["performed"] = True
        except Exception as e:
            entry["error"] = str(e)
        log.append(entry)
    return log
