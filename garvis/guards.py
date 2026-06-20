"""Deterministic safety guards — enforced in code, never left to the LLM."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .config import Config
from .gather import Item

PROTECTED_LABELS = {"STARRED", "IMPORTANT"}
OTP_MARKERS = (
    "one-time code", "one time code", "verification code", "security code",
    "passcode", "otp", "2fa", "your code is", "login code", "auth code",
)


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:                                  # RFC 2822, e.g. Gmail date header
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    try:                                  # ISO 8601, e.g. Outlook receivedDateTime
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_old(item: Item) -> float | None:
    dt = _parse_date(item.date)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).total_seconds() / 60.0


def looks_like_otp(item: Item) -> bool:
    hay = f"{item.subject} {item.snippet}".lower()
    return any(m in hay for m in OTP_MARKERS)


def otp_is_deletable(item: Item, cfg: Config) -> bool:
    """An OTP is deletable only once it is older than the grace window."""
    if not looks_like_otp(item):
        return False
    age = minutes_old(item)
    grace = cfg.raw.get("otp_grace_minutes", 5)
    return age is not None and age > grace


def protected_reason(item: Item, cfg: Config) -> str | None:
    """Return why an item is protected (must never be deleted), or None."""
    if item.source == "messages":
        return "text message"
    if any(lbl in PROTECTED_LABELS for lbl in item.labels):
        return "starred/important label"
    if item.has_attachments:
        return "has attachment (likely a document)"
    # Fresh (or unknown-age) one-time codes are protected; expired ones are not.
    if looks_like_otp(item) and not otp_is_deletable(item, cfg):
        return "fresh one-time code (within grace window)"

    sender = (item.sender or "").lower()
    for vip in cfg.raw.get("vip_senders", []) or []:
        if vip.lower() in sender:
            return f"VIP sender ({vip})"

    haystack = f"{item.subject} {item.snippet}".lower()
    for kw in cfg.raw.get("protected_keywords", []) or []:
        # Word-boundary match so "lease" doesn't fire on "Please"/"Release".
        if re.search(rf"\b{re.escape(kw.lower())}\b", haystack):
            return f"protected keyword ({kw})"

    # Garvis's own digests
    if "garvis digest" in (item.subject or "").lower():
        return "garvis digest"
    return None
