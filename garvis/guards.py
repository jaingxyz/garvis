"""Deterministic safety guards — enforced in code, never left to the LLM."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from .config import Config
from .dates import _parse_date
from .gather import Item

PROTECTED_LABELS = {"STARRED", "IMPORTANT"}
OTP_MARKERS = (
    "one-time code", "one time code", "one-time password", "one time password",
    "verification code", "security code", "id code", "access code", "confirmation code",
    "passcode", "otp", "2fa", "your code is", "login code", "auth code",
    "one-time pin", "one time pin", "security pin",
)
# Whole-word/phrase match: "otp" must not fire inside "footprint", "2fa" inside a hash, etc.
_OTP_RE = re.compile(r"\b(?:" + "|".join(re.escape(m) for m in OTP_MARKERS) + r")\b", re.I)


def minutes_old(item: Item) -> float | None:
    # Sources without timestamps (SMS/WhatsApp) fall back to when Garvis first saw the
    # snippet, so a one-time code ages out over successive runs instead of never expiring.
    dt = _parse_date(item.date) or _parse_date(item.first_seen)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (datetime.now(UTC) - dt).total_seconds() / 60.0


def looks_like_otp(item: Item) -> bool:
    return _OTP_RE.search(f"{item.subject} {item.snippet}") is not None


def otp_is_deletable(item: Item, cfg: Config) -> bool:
    """An OTP is deletable only once it is older than the grace window."""
    if not looks_like_otp(item):
        return False
    age = minutes_old(item)
    grace = cfg.raw.get("otp_grace_minutes", 5)
    return age is not None and age > grace


def protected_reason(item: Item, cfg: Config) -> str | None:
    """Return why an item is protected (must never be deleted), or None.

    SMS and WhatsApp now receive full classification (PROMOTION/UPDATE etc.)
    instead of automatic protection by source.
    """
    # Garvis's own digests, before anything else: their text quotes cleanup reasons and
    # code phrases that would otherwise trip the OTP handling below.
    if "garvis digest" in (item.subject or "").lower():
        return "garvis digest"
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

    # Keyword protection is meant for live business (an invoice to pay, a security alert to
    # act on). On a text thread that has been silent for months it only preserves junk — a
    # year-old "your password was changed" SMS is not worth keeping. Labels, attachments,
    # VIP senders and fresh one-time codes above are unaffected by this.
    kw_days = cfg.raw.get("sms_keyword_protection_days")
    if item.source == "messages" and kw_days is not None:
        age = minutes_old(item)
        if age is not None and age > float(kw_days) * 24 * 60:
            return None

    haystack = f"{item.subject} {item.snippet}".lower()
    if otp_is_deletable(item, cfg):
        # An expired code's own wording must not keep it alive ("one-time password" vs. the
        # "password" keyword), so blank out just the code phrases. Every other keyword in the
        # text ("lease", "invoice", ...) still protects it.
        haystack = _OTP_RE.sub(" ", haystack)
    # Some keywords only make sense for mail. "password" guards a reset link or a breach
    # notice you may need; the same word in a text is almost always a spent alert ("your
    # password was changed", "enter this code to update your user ID"), so the owner can
    # switch it off per source rather than deleting it from protected_keywords entirely.
    ignored = {str(k).strip().lower()
               for k in (cfg.raw.get("sms_unprotected_keywords", []) or [])}
    for kw in cfg.raw.get("protected_keywords", []) or []:
        if item.source == "messages" and kw.strip().lower() in ignored:
            continue
        # Word-boundary match so "lease" doesn't fire on "Please"/"Release".
        if re.search(rf"\b{re.escape(kw.lower())}\b", haystack):
            return f"protected keyword ({kw})"
    return None
