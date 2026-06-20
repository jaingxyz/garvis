"""Basic guard tests (example-based, per review)."""
from garvis.gather import Item
from garvis.guards import protected_reason


def test_protected_vip():
    item = Item(source="gmail", id="1", subject="test", sender="partner@example.com", date="", snippet="")
    cfg = type("obj", (object,), {"raw": {"vip_senders": ["partner@example.com"]}})()
    assert protected_reason(item, cfg) == "VIP sender (partner@example.com)"


def test_protected_keyword():
    item = Item(source="gmail", id="1", subject="Invoice for services", sender="foo@bar.com", date="", snippet="")
    cfg = type("obj", (object,), {"raw": {"protected_keywords": ["invoice"]}})()
    assert protected_reason(item, cfg) == "protected keyword (invoice)"
