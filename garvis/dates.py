"""Date-string parsing shared across modules.

Lives in its own module (with no internal garvis imports) so that date parsing
can be used by gather, guards, and store without creating an import cycle.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime


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


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}\s*(am|pm)?$", re.I)
_RELATIVE = re.compile(r"\b(\d+)\s*(min|minute|hr|hour|day|week|month|year)s?\b", re.I)
_UNIT_MINUTES = {"min": 1, "minute": 1, "hr": 60, "hour": 60, "day": 1440,
                 "week": 10080, "month": 43200, "year": 525600}


def parse_ui_timestamp(s: str, *, now: datetime | None = None) -> datetime | None:
    """Parse a Google Messages row timestamp into a datetime, or None.

    The web UI abbreviates by age, so the same field can be any of:
      "9:49 PM"                      → today
      "Thu" / "Thursday"             → that weekday within the last 7 days
      "Oct 23"                       → this year (or last year if that would be future)
      "Oct 23, 2025" / "10/23/2025"  → explicit
      "Thursday, October 23, 2025, 9:49 PM"  → title/aria-label form
    Returns a timezone-aware datetime in the local zone. Anything unrecognised is None,
    which callers treat as "age unknown" (and therefore keep).
    """
    text = (s or "").strip()
    if not text:
        return None
    now = now or datetime.now(UTC).astimezone()
    tz = now.tzinfo

    rel = _RELATIVE.search(text)                   # "56 min", "2 hr", "3 days ago"
    if rel:
        return now - timedelta(minutes=int(rel.group(1)) * _UNIT_MINUTES[rel.group(2).lower()])

    low_all = text.lower()
    if "yesterday" in low_all:
        return (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    if "today" in low_all or re.search(r"\bnow\b", low_all):
        return now

    if _TIME_ONLY.match(text):
        for fmt in ("%I:%M %p", "%H:%M"):
            try:
                t = datetime.strptime(text.upper(), fmt)
            except ValueError:
                continue
            dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            return dt - timedelta(days=1) if dt > now else dt
        return None

    # A weekday anywhere in the text, unless a month name is also present (then it is a
    # full date like "Thursday, October 23, 2025", handled below). The UI sometimes renders
    # the full and abbreviated names back to back ("SaturdaySat"), so search, don't match.
    low = text.lower().rstrip(".")
    has_month = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", low)
    if not has_month and not re.search(r"\d", low):
        for i, day in enumerate(_WEEKDAYS):
            # No right-hand boundary on the full name: the UI may run the two spellings
            # together ("saturdaysat"). The abbreviation still needs both boundaries.
            if re.search(rf"\b{day}|\b{day[:3]}\b", low):
                delta = (now.weekday() - i) % 7 or 7   # the most recent past occurrence
                return (now - timedelta(days=delta)).replace(
                    hour=12, minute=0, second=0, microsecond=0)

    # Strip a leading weekday name and any time part from the fuller title form.
    body = re.sub(r"^[A-Za-z]+day,\s*", "", text)
    body = re.sub(r"[, ]+\d{1,2}:\d{2}\s*(AM|PM)?$", "", body, flags=re.I).strip().rstrip(",")

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(body, fmt).replace(tzinfo=tz)
        except ValueError:
            pass
    for fmt in ("%b %d %Y", "%B %d %Y"):           # no year → this year, else last year
        try:                                       # year supplied so Feb 29 can't fail
            d = datetime.strptime(f"{body} {now.year}", fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        return d.replace(year=now.year - 1) if d > now + timedelta(days=1) else d
    return None
