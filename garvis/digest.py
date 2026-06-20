"""Render the digest, write it to disk, and email a copy."""
from __future__ import annotations

from datetime import datetime

from .config import Config
from .gather import Item
from .mcp_client import Tools


def render(cfg: Config, ts: datetime, priorities_md: str,
           items: list[Item], cleanup_log: list[dict], texts_ok: bool) -> str:
    updates = [i for i in items if i.label == "UPDATE"]
    waiting = [i for i in items if i.label == "WAITING"]
    review = [i for i in items if i.label == "UNSURE"]
    mode = "DRY-RUN (nothing deleted)" if cfg.dry_run else "live"

    out = [f"# Garvis digest — {ts:%Y-%m-%d %H:%M %Z}", f"_Mode: {mode}_", ""]
    out += ["## Your briefing", "", priorities_md, ""]

    out.append("## Waiting on others (you replied — their move)")
    out += [f"- {i.subject} — {i.sender} ({i.reason})" for i in waiting] or ["- none"]
    out.append("")

    out.append("## Updates summarized")
    out += [f"- {i.summary or i.subject} ({i.source})" for i in updates] or ["- none"]
    out.append("")

    out.append("## Cleanup log (recoverable ~30 days)")
    out.append("| Account | Sender | Subject | Reason | Id | Status |")
    out.append("|---|---|---|---|---|---|")
    for e in cleanup_log:
        status = "would delete" if e.get("dry_run") else (
            "deleted" if e.get("performed") else f"ERROR {e.get('error','')}")
        out.append(f"| {e['account']} | {e['sender']} | {e['subject']} | "
                   f"{e['reason']} | {e['id'][:16]}… | {status} |")
    if not cleanup_log:
        out.append("| — | — | — | — | — | nothing |")
    out.append("")

    out.append("## Needs your review (kept, low confidence)")
    out += [f"- {i.subject} — {i.sender} ({i.reason})" for i in review] or ["- none"]
    out.append("")

    counts = {}
    for i in items:
        counts[i.source] = counts.get(i.source, 0) + 1
    out.append("## Stats")
    out.append(f"- Scanned: {dict(counts)}")
    out.append(f"- Cleanup entries: {len(cleanup_log)} | Texts: "
               f"{'ok' if texts_ok else 'unavailable'}")
    return "\n".join(out)


def write_file(cfg: Config, ts: datetime, md: str) -> str:
    d = cfg.path("digests")
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{ts:%Y-%m-%d-%H%M}.md"
    fp.write_text(md)
    return str(fp)


async def email_copy(tools: Tools, cfg: Config, ts: datetime, md: str) -> None:
    await tools.call(
        "gmail_send",
        to=[cfg.owner_gmail],
        subject=f"Garvis digest — {ts:%Y-%m-%d %H:%M}",
        body=md,
    )
