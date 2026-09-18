"""Store tests: first-seen stamping for undated sources."""
from garvis.gather import Item
from garvis.store import Store


def _sms(snippet, name="938763"):
    return Item(source="messages", id=name, subject=name, sender=name, date="", snippet=snippet)


def test_stamp_first_seen_persists_and_keys_by_snippet(tmp_path):
    store = Store(tmp_path / "t.db")
    a = _sms("111111 is your code")
    store.stamp_first_seen([a])
    assert a.first_seen

    # Same snippet seen again keeps its original first_seen (so it can age out).
    a2 = _sms("111111 is your code")
    store.stamp_first_seen([a2])
    assert a2.first_seen == a.first_seen

    # A different code in the same thread starts its own window.
    b = _sms("222222 is your code")
    store.stamp_first_seen([b])
    assert b.first_seen >= a.first_seen and b.first_seen != "" and b.first_seen is not None


def test_stamp_first_seen_skips_dated_items(tmp_path):
    store = Store(tmp_path / "t.db")
    mail = Item(source="gmail", id="m1", subject="hi", sender="x@y.z",
                date="Wed, 17 Sep 2026 07:00:00 -0700", snippet="hello")
    store.stamp_first_seen([mail])
    assert mail.first_seen == ""
