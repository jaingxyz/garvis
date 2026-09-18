"""Guard tests: protection ordering, OTP matching and the grace window."""
from conftest import ago, make_cfg

from garvis.gather import Item
from garvis.guards import looks_like_otp, protected_reason


def _mail(subject="test", sender="foo@bar.com", snippet="", date="", **kw):
    return Item(source="gmail", id="1", subject=subject, sender=sender, date=date, snippet=snippet, **kw)


def test_protected_vip():
    cfg = make_cfg(vip_senders=["partner@example.com"])
    assert protected_reason(_mail(sender="partner@example.com"), cfg) == "VIP sender (partner@example.com)"


def test_protected_keyword():
    cfg = make_cfg(protected_keywords=["invoice"])
    assert protected_reason(_mail("Invoice for services"), cfg) == "protected keyword (invoice)"


def _otp_sms(**kw):
    return Item(source="messages", id="938763", subject="938763", sender="938763", date="",
                snippet="064459 is your verification code. Do not share it.", **kw)


def test_otp_unknown_age_is_protected():
    cfg = make_cfg(otp_grace_minutes=5)
    assert protected_reason(_otp_sms(), cfg) == "fresh one-time code (within grace window)"


def test_otp_ages_out_via_first_seen():
    cfg = make_cfg(otp_grace_minutes=5)
    assert protected_reason(_otp_sms(first_seen=ago(minutes=1)), cfg) is not None
    assert protected_reason(_otp_sms(first_seen=ago(minutes=30)), cfg) is None


def test_otp_markers_match_whole_words_only():
    assert not looks_like_otp(_mail("Reduce your carbon footprint"))       # "otp" inside a word
    assert not looks_like_otp(_mail(snippet="sha 9c2fa1"))                  # "2fa" inside a token
    assert looks_like_otp(_mail(snippet="Your OTP is 123456"))
    assert looks_like_otp(_mail(snippet="Enable 2FA today"))
    assert looks_like_otp(_mail(snippet="One-Time Password: 4321"))


def test_expired_one_time_password_bypasses_only_its_own_wording():
    cfg = make_cfg(otp_grace_minutes=5, protected_keywords=["password", "lease"],
                   vip_senders=["partner@example.com"])
    otp = Item(source="messages", id="262966", subject="262966", sender="262966", date="",
               snippet="Your one-time password for delivery is 4321", first_seen=ago(minutes=30))
    assert protected_reason(otp, cfg) is None
    # Fresh: still protected. VIP sender: still protected even when expired.
    otp.first_seen = ago(minutes=1)
    assert protected_reason(otp, cfg) == "fresh one-time code (within grace window)"
    otp.first_seen, otp.sender = ago(minutes=30), "partner@example.com"
    assert protected_reason(otp, cfg) == "VIP sender (partner@example.com)"
    # Another protected keyword in the same text still protects an expired code.
    otp.sender = "262966"
    otp.snippet = "Storage passcode is 4321, keep it with your lease"
    assert protected_reason(otp, cfg) == "protected keyword (lease)"
    # A real password email without OTP wording keeps its keyword protection.
    mail = _mail("Reset your password", "a@b.c", "click to reset",
                 date="Wed, 17 Sep 2026 01:00:00 -0700")
    assert protected_reason(mail, cfg) == "protected keyword (password)"


def test_own_digest_protected_before_otp_handling():
    cfg = make_cfg(otp_grace_minutes=5)
    digest = _mail("Garvis digest — 2026-09-17 09:26 PDT", "me@example.com",
                   "Cleanup log: 938763 | Expired one-time code | deleted",
                   date="Wed, 17 Sep 2026 01:00:00 -0700")
    assert protected_reason(digest, cfg) == "garvis digest"
