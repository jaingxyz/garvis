"""Cleanup action tests: source-specific gates, the SMS delete path, result checking."""
from conftest import FakeTools, ago, make_cfg

from garvis.actions import cleanup, sms_cleanup_reason
from garvis.gather import Item


def _sms(label="PROMOTION", name="(555) 010-0000", snippet="Big sale this weekend!",
         owner_replied=False, first_seen=""):
    return Item(source="messages", id=name, subject=name, sender=name, date="",
                snippet=snippet, label=label, owner_replied=owner_replied, first_seen=first_seen)


def _cfg(**raw):
    return make_cfg(allow_sms_delete=True, **raw)


async def test_sms_not_deleted_without_opt_in():
    tools = FakeTools()
    assert await cleanup(tools, make_cfg(), [_sms()]) == []
    assert tools.calls == []


async def test_sms_deleted_when_opted_in():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(), [_sms()])
    assert tools.calls == [("delete_conversation", {"name": "(555) 010-0000"})], log
    assert len(log) == 1 and log[0]["performed"] is True and "error" not in log[0]


async def test_sms_update_and_concluded_follow_email_policy():
    tools = FakeTools()
    items = [_sms("UPDATE", "12345", "Your package shipped"),
             _sms("CONCLUDED", "(206) 555-0100", "thanks, all set"),
             _sms("ACTIONABLE", "(206) 555-0101", "can you call me back?")]
    await cleanup(tools, _cfg(), items)
    assert [kw["name"] for _, kw in tools.calls] == ["12345", "(206) 555-0100"]


async def test_sms_threads_owner_replied_in_are_never_trashed():
    tools = FakeTools()
    items = [_sms("PROMOTION", "(555) 010-0001", owner_replied=True),
             _sms("UPDATE", "12345", "shipped", owner_replied=True, first_seen=ago(days=30)),
             _sms("UNSURE", "(555) 010-0002", "hmm", owner_replied=None)]   # unknown → keep
    assert await cleanup(tools, _cfg(), items) == []
    assert tools.calls == []


async def test_sms_unknown_number_unsure_is_trashed_now():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(), [_sms("UNSURE", "(555) 010-0003", "Payment posted")])
    assert [kw["name"] for _, kw in tools.calls] == ["(555) 010-0003"]
    assert log[0]["reason"] == "Unknown number (never replied)"


async def test_sms_actionable_from_unknown_number_waits_out_stale_days():
    cfg = _cfg(stale_notification_days=7)
    fresh = _sms("ACTIONABLE", "(555) 010-0004", "Pay your bill", first_seen=ago(days=2))
    stale = _sms("PERSONAL", "(555) 010-0005", "Pay your bill", first_seen=ago(days=8))
    tools = FakeTools()
    log = await cleanup(tools, cfg, [fresh, stale])
    assert [kw["name"] for _, kw in tools.calls] == ["(555) 010-0005"]
    assert log[0]["reason"].startswith("Stale notification")
    # No first_seen at all → age unknown → not stale.
    assert await cleanup(FakeTools(), cfg, [_sms("ACTIONABLE", "(555) 010-0006", "Pay")]) == []


async def test_sms_named_contacts_are_never_trashed():
    tools = FakeTools()
    items = [_sms("PROMOTION", "Sam Vendor", "50% off this weekend"),
             _sms("UPDATE", "Alex Example", "your code is 123456"),
             _sms("UNSURE", "Realtor, Pat Partner", "your verification code is 9"),
             _sms("PROMOTION", "Séverine", "non-ascii name"),
             _sms("PROMOTION", "", "no name at all")]
    for it in items:
        it.first_seen = ago(minutes=30)
    assert await cleanup(tools, _cfg(otp_grace_minutes=5), items) == []
    assert tools.calls == []


async def test_listed_notification_sender_and_pattern_override_contact_guard():
    cfg = _cfg(sms_notification_senders=["Carrier Alerts"],
               sms_notification_patterns=[r"voice ?mail"])
    items = [_sms("ACTIONABLE", "carrier alerts", "Your bill is ready"),
             _sms("ACTIONABLE", "Owner Self", "New voicemail from +1 555 010 0100"),
             _sms("PERSONAL", "Owner Self", "lunch?"),                      # pattern misses
             _sms("UPDATE", "Owner Self", "New voicemail", owner_replied=True)]  # replied
    tools = FakeTools()
    log = await cleanup(tools, cfg, items)
    assert [kw["name"] for _, kw in tools.calls] == ["carrier alerts", "Owner Self"]
    assert log[0]["reason"].startswith("Notification (sender (Carrier Alerts)")
    assert log[1]["reason"].startswith("Notification (pattern (voice ?mail)")


def test_sms_cleanup_reason_candidate_mode_ignores_reply_state():
    it = _sms("UPDATE", "12345", owner_replied=None)
    assert sms_cleanup_reason(it, _cfg()) is None
    assert sms_cleanup_reason(it, _cfg(), assume_never_replied=True) == "Update"


async def test_sms_dry_run_logs_but_does_not_call():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(dry_run=True), [_sms()])
    assert tools.calls == []
    assert len(log) == 1 and log[0]["performed"] is False and log[0]["dry_run"] is True


async def test_sms_fresh_otp_is_protected_even_when_opted_in():
    tools = FakeTools()
    otp = _sms("UPDATE", "938763", "064459 is your verification code. Do not share it.")
    assert await cleanup(tools, _cfg(), [otp]) == []
    assert tools.calls == []


async def test_expired_otp_is_trashed_even_if_model_said_unsure():
    tools = FakeTools()
    otp = _sms("UNSURE", "938763", "064459 is your verification code. Do not share it.",
               first_seen=ago(minutes=30))
    log = await cleanup(tools, _cfg(otp_grace_minutes=5), [otp])
    assert tools.calls == [("delete_conversation", {"name": "938763"})]
    assert log[0]["reason"] == "Expired one-time code"
    # ...but a genuinely uncertain thread from a saved contact is still kept.
    tools = FakeTools()
    assert await cleanup(tools, _cfg(), [_sms("UNSURE", "Ann", "hey?")]) == []
    assert tools.calls == []


async def test_in_band_tool_error_is_not_recorded_as_performed():
    """The MCP adapter returns server errors as text instead of raising."""
    for bad in ("Error: \"12345\" matches multiple conversations exactly",
                {"ok": False, "error": "still in the list"},
                {"isError": True},
                {"error": "boom"}):
        tools = FakeTools(result=bad)
        log = await cleanup(tools, _cfg(), [_sms("UPDATE", "12345", "shipped")])
        assert len(tools.calls) == 1
        assert log[0]["performed"] is False and log[0]["error"], bad


async def test_email_delete_failure_is_logged_not_performed():
    mail = Item(source="gmail", id="m1", subject="Sale", sender="promo@x.y",
                date="Wed, 17 Sep 2026 01:00:00 -0700", snippet="buy", label="PROMOTION")
    log = await cleanup(FakeTools(raise_exc=RuntimeError("quota")), make_cfg(), [mail])
    assert log == [{**log[0], "performed": False, "error": "quota"}]
