"""Cleanup action tests: source-specific gates and the SMS delete path."""
from datetime import UTC, datetime, timedelta

from garvis.actions import cleanup
from garvis.gather import Item


class FakeTools:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool_name, **kwargs):   # same signature as mcp_client.Tools.call
        self.calls.append((tool_name, kwargs))
        return {"ok": True}


def _cfg(**raw):
    base = {"dry_run": False}
    base.update(raw)
    return type("obj", (object,), {"raw": base, "dry_run": base["dry_run"]})()


def _sms(label="PROMOTION", name="(555) 010-0000", snippet="Big sale this weekend!",
         owner_replied=False, first_seen=""):
    return Item(source="messages", id=name, subject=name, sender=name, date="",
                snippet=snippet, label=label, owner_replied=owner_replied, first_seen=first_seen)


def _ago(**kw):
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


async def test_sms_not_deleted_without_opt_in():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(), [_sms()])
    assert log == []
    assert tools.calls == []


async def test_sms_deleted_when_opted_in():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(allow_sms_delete=True), [_sms()])
    assert tools.calls == [("delete_conversation", {"name": "(555) 010-0000"})], log
    assert len(log) == 1 and log[0]["performed"] is True and "error" not in log[0]


async def test_sms_update_and_concluded_follow_email_policy():
    tools = FakeTools()
    items = [_sms("UPDATE", "12345", "Your package shipped"),
             _sms("CONCLUDED", "(206) 555-0100", "thanks, all set"),
             _sms("ACTIONABLE", "(206) 555-0101", "can you call me back?")]
    await cleanup(tools, _cfg(allow_sms_delete=True), items)
    assert [kw["name"] for _, kw in tools.calls] == ["12345", "(206) 555-0100"]


async def test_sms_threads_owner_replied_in_are_never_trashed():
    tools = FakeTools()
    items = [_sms("PROMOTION", "(555) 010-0001", owner_replied=True),
             _sms("UPDATE", "12345", "shipped", owner_replied=True, first_seen=_ago(days=30)),
             _sms("UNSURE", "(555) 010-0002", "hmm", owner_replied=None)]   # unknown → keep
    log = await cleanup(tools, _cfg(allow_sms_delete=True), items)
    assert tools.calls == [] and log == []


async def test_sms_unknown_number_unsure_is_trashed_now():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(allow_sms_delete=True),
                        [_sms("UNSURE", "(555) 010-0003", "Your payment of $12 posted")])
    assert [kw["name"] for _, kw in tools.calls] == ["(555) 010-0003"]
    assert log[0]["reason"] == "Unknown number (never replied)"


async def test_sms_actionable_from_unknown_number_waits_out_stale_days():
    cfg = _cfg(allow_sms_delete=True, stale_notification_days=7)
    fresh = _sms("ACTIONABLE", "(555) 010-0004", "Pay your bill", first_seen=_ago(days=2))
    stale = _sms("PERSONAL", "(555) 010-0005", "Pay your bill", first_seen=_ago(days=8))
    tools = FakeTools()
    log = await cleanup(tools, cfg, [fresh, stale])
    assert [kw["name"] for _, kw in tools.calls] == ["(555) 010-0005"]
    assert log[0]["reason"].startswith("Stale notification")
    # No first_seen at all → age unknown → not stale.
    tools = FakeTools()
    assert await cleanup(tools, cfg, [_sms("ACTIONABLE", "(555) 010-0006", "Pay")]) == []


async def test_sms_named_contacts_are_never_trashed():
    tools = FakeTools()
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    items = [_sms("PROMOTION", "Sam Vendor", "50% off this weekend"),
             _sms("UPDATE", "Alex Example", "your code is 123456"),
             _sms("UNSURE", "Realtor, Pat Partner", "your verification code is 9"),
             _sms("PROMOTION", "Séverine", "non-ascii name")]
    for it in items:
        it.first_seen = stale
    log = await cleanup(tools, _cfg(allow_sms_delete=True, otp_grace_minutes=5), items)
    assert tools.calls == [] and log == []


async def test_sms_dry_run_logs_but_does_not_call():
    tools = FakeTools()
    log = await cleanup(tools, _cfg(dry_run=True, allow_sms_delete=True), [_sms()])
    assert tools.calls == []
    assert len(log) == 1 and log[0]["performed"] is False and log[0]["dry_run"] is True


async def test_sms_fresh_otp_is_protected_even_when_opted_in():
    tools = FakeTools()
    otp = _sms("UPDATE", "938763", "064459 is your verification code. Do not share it.")
    log = await cleanup(tools, _cfg(allow_sms_delete=True), [otp])
    assert log == []
    assert tools.calls == []


async def test_expired_otp_is_trashed_even_if_model_said_unsure():
    tools = FakeTools()
    stale = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    otp = _sms("UNSURE", "938763", "064459 is your verification code. Do not share it.")
    otp.first_seen = stale
    log = await cleanup(tools, _cfg(allow_sms_delete=True, otp_grace_minutes=5), [otp])
    assert tools.calls == [("delete_conversation", {"name": "938763"})]
    assert log[0]["reason"] == "Expired one-time code"

    # ...but a genuinely uncertain, non-OTP thread is still kept.
    tools = FakeTools()
    log = await cleanup(tools, _cfg(allow_sms_delete=True), [_sms("UNSURE", "Ann", "hey?")])
    assert tools.calls == [] and log == []
