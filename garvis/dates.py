"""Date-string parsing shared across modules.

Lives in its own module (with no internal garvis imports) so that date parsing
can be used by gather, guards, and store without creating an import cycle.
"""
from __future__ import annotations

from datetime import datetime
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
