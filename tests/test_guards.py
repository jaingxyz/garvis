"""Basic guard tests (example-based, per review)."""
from datetime import UTC, datetime, timedelta

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


def _otp_sms(**kw):
    return Item(source="messages", id="938763", subject="938763", sender="938763", date="",
                snippet="064459 is your verification code. Do not share it.", **kw)


def test_otp_unknown_age_is_protected():
    cfg = type("obj", (object,), {"raw": {"otp_grace_minutes": 5}})()
    assert protected_reason(_otp_sms(), cfg) == "fresh one-time code (within grace window)"


def test_otp_ages_out_via_first_seen():
    cfg = type("obj", (object,), {"raw": {"otp_grace_minutes": 5}})()
    fresh = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    assert protected_reason(_otp_sms(first_seen=fresh), cfg) is not None
    assert protected_reason(_otp_sms(first_seen=stale), cfg) is None


def test_expired_one_time_password_bypasses_password_keyword():
    cfg = type("obj", (object,), {"raw": {"otp_grace_minutes": 5,
                                           "protected_keywords": ["password"],
                                           "vip_senders": ["partner@example.com"]}})()
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    otp = Item(source="messages", id="262966", subject="262966", sender="262966", date="",
               snippet="Your one-time password for delivery is 4321", first_seen=stale)
    assert protected_reason(otp, cfg) is None
    # Fresh: still protected. VIP sender: still protected even when expired.
    otp.first_seen = datetime.now(UTC).isoformat()
    assert protected_reason(otp, cfg) == "fresh one-time code (within grace window)"
    otp.first_seen, otp.sender = stale, "partner@example.com"
    assert protected_reason(otp, cfg) == "VIP sender (partner@example.com)"
    # A real password email without OTP wording keeps its keyword protection.
    mail = Item(source="gmail", id="m", subject="Reset your password", sender="a@b.c",
                date="Wed, 17 Sep 2026 01:00:00 -0700", snippet="click to reset")
    assert protected_reason(mail, cfg) == "protected keyword (password)"
