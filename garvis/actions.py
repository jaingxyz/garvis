"""Cleanup actions (soft-delete) with dry-run + safety guards."""
from __future__ import annotations

from .config import Config
from .gather import Item
from .guards import protected_reason
from .mcp_client import Tools

DELETABLE = {"PROMOTION", "UPDATE", "CONCLUDED"}


async def cleanup(tools: Tools, cfg: Config, items: list[Item]) -> list[dict]:
    """Soft-delete deletable items. Returns a log of (intended or performed) deletions."""
    log = []
    for it in items:
        if it.label not in DELETABLE or protected_reason(it, cfg):
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
            entry["performed"] = True
        except Exception as e:
            entry["error"] = str(e)
        log.append(entry)
    return log
