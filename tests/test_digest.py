"""Tests for digest rendering, incl. the 'Waiting on others' section."""
from datetime import datetime

from garvis.config import Config
from garvis.digest import render
from garvis.gather import Item


def _item(label: str, subject: str, sender: str, reason: str = "") -> Item:
    return Item(source="gmail", id=subject, subject=subject, sender=sender,
                date="2026-06-20", snippet="", thread_id=subject, label=label, reason=reason)


def test_waiting_section_lists_waiting_items():
    cfg = Config.load("config.example.yaml")
    cfg.raw["dry_run"] = True
    items = [
        _item("WAITING", "Quote follow-up", "vendor@ex.com", "you asked; no reply yet"),
        _item("UPDATE", "Receipt", "store@ex.com"),
    ]
    md = render(cfg, datetime(2026, 6, 20, 9, 0), "briefing", items, [], texts_ok=True)

    assert "## Waiting on others" in md
    assert "Quote follow-up" in md
    assert "vendor@ex.com" in md
    # an UPDATE must not leak into the waiting section
    assert md.index("Quote follow-up") < md.index("## Updates summarized")


def test_waiting_section_empty_shows_none():
    cfg = Config.load("config.example.yaml")
    cfg.raw["dry_run"] = True
    md = render(cfg, datetime(2026, 6, 20, 9, 0),
                "briefing", [_item("UPDATE", "Receipt", "store@ex.com")], [], texts_ok=True)

    waiting = md.split("## Waiting on others")[1].split("## Updates summarized")[0]
    assert "- none" in waiting
