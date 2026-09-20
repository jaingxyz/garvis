"""Google Messages row timestamps: abbreviated by age, so every form must parse."""
from datetime import datetime, timedelta, timezone

from garvis.dates import parse_ui_timestamp

# A Sunday, 3:30 PM local (fixed offset so the assertions don't depend on the host zone).
NOW = datetime(2026, 9, 20, 15, 30, tzinfo=timezone(timedelta(hours=-7)))


def p(s):
    return parse_ui_timestamp(s, now=NOW)


def test_time_only_is_today():
    dt = p("9:49 AM")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (NOW.year, NOW.month, NOW.day, 9, 49)


def test_time_later_than_now_is_yesterday():
    dt = p("11:58 PM")
    assert dt < NOW and (NOW - dt) < timedelta(days=1)


def test_weekday_is_the_most_recent_past_one():
    dt = p("Thu")
    assert dt.weekday() == 3 and 0 < (NOW - dt).days <= 7
    assert p("Thursday").date() == dt.date()
    # Today's own weekday name means a week ago, not today.
    assert (NOW - p("Sunday")).days == 7


def test_explicit_dates():
    assert p("Oct 23, 2025").date() == datetime(2025, 10, 23).date()
    assert p("October 23, 2025").date() == datetime(2025, 10, 23).date()
    assert p("10/23/2025").date() == datetime(2025, 10, 23).date()
    assert p("Thursday, October 23, 2025, 9:49 PM").date() == datetime(2025, 10, 23).date()


def test_month_day_without_year_never_lands_in_the_future():
    assert p("Sep 18").date() == datetime(2026, 9, 18).date()      # this year
    assert p("Dec 25").date() == datetime(2025, 12, 25).date()     # would be future → last year


def test_relative_and_word_forms():
    assert (NOW - p("56 min")) == timedelta(minutes=56)
    assert (NOW - p("2 hr")) == timedelta(hours=2)
    assert (NOW - p("3 days ago")) == timedelta(days=3)
    assert p("Yesterday").date() == (NOW - timedelta(days=1)).date()
    assert p("just now") == NOW


def test_doubled_weekday_text_from_the_ui():
    """The row renders the full and abbreviated name back to back."""
    assert p("SaturdaySat").date() == p("Saturday").date()
    assert p("ThursdayThu").weekday() == 3


def test_unparseable_is_none():
    for s in ("", "   ", "???", "sometime"):
        assert p(s) is None
