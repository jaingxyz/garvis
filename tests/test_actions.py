"""Cleanup action tests: source-specific gates, the SMS delete path, result checking."""
from conftest import FakeTools, ago, make_cfg

from garvis.actions import cleanup, sms_cleanup_reason, sms_label_free_reason
from garvis.gather import Item


def _sms(label="PROMOTION", name="(555) 010-0000", snippet="Big sale this weekend!",
         owner_replied=False, first_seen="", unread=None):
    return Item(source="messages", id=name, subject=name, sender=name, date="",
                snippet=snippet, label=label, owner_replied=owner_replied,
                first_seen=first_seen, unread=unread)


COMPLETED = [r"\b(was|been|is)? ?delivered\b", r"\bdelivery (is |was )?complete",
             r"\b(repair|order|service|installation|appointment) is complete"]


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
    # All recent: the age-based rules below must not fire, so this isolates the reply guard.
    items = [_sms("PROMOTION", "(555) 010-0001", owner_replied=True, first_seen=ago(hours=2)),
             _sms("UPDATE", "12345", "shipped", owner_replied=True, first_seen=ago(hours=2)),
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


async def test_completed_delivery_alert_goes_even_if_owner_replied():
    cfg = _cfg(sms_completed_patterns=COMPLETED)
    items = [_sms("UPDATE", "12345", "Your package was delivered.", owner_replied=True),
             _sms("ACTIONABLE", "94079", "Your repair is complete. Need more help?",
                  owner_replied=True),
             # in-flight updates must NOT match
             _sms("UPDATE", "(844) 555-0111", "Your package is out for delivery.",
                  owner_replied=True),
             _sms("UPDATE", "(844) 555-0112", "Arriving today by 9pm.", owner_replied=True)]
    tools = FakeTools()
    log = await cleanup(tools, cfg, items)
    assert [kw["name"] for _, kw in tools.calls] == ["12345", "94079"]
    assert log[0]["reason"].startswith("Completed delivery/service alert")


async def test_read_and_stale_notification_goes_even_if_owner_replied():
    cfg = _cfg(sms_read_stale_days=3)
    old_read = _sms("ACTIONABLE", "(415) 555-0100", "See you Monday at 11:00 AM",
                    owner_replied=True, first_seen=ago(days=5), unread=False)
    recent_read = _sms("ACTIONABLE", "(415) 555-0101", "See you Monday",
                       owner_replied=True, first_seen=ago(days=1), unread=False)
    old_unread = _sms("ACTIONABLE", "(415) 555-0102", "See you Monday",
                      owner_replied=True, first_seen=ago(days=5), unread=True)
    unknown_read_state = _sms("ACTIONABLE", "(415) 555-0103", "See you Monday",
                              owner_replied=True, first_seen=ago(days=5))
    tools = FakeTools()
    log = await cleanup(tools, cfg, [old_read, recent_read, old_unread, unknown_read_state])
    assert [kw["name"] for _, kw in tools.calls] == ["(415) 555-0100"]
    assert log[0]["reason"].startswith("Read notification")


async def test_listed_business_thread_read_and_stale_goes():
    cfg = _cfg(sms_read_stale_days=3, sms_notification_senders=["Xfinity Assistant", "Luma"])
    items = [_sms("PROMOTION", "Xfinity Assistant", "View on your phone",
                  owner_replied=True, first_seen=ago(days=9), unread=False),
             # a real person, same age and read state, stays
             _sms("PERSONAL", "Pat Partner", "ok see you", owner_replied=True,
                  first_seen=ago(days=9), unread=False)]
    tools = FakeTools()
    await cleanup(tools, cfg, items)
    assert [kw["name"] for _, kw in tools.calls] == ["Xfinity Assistant"]


async def test_fresh_otp_still_protected_when_read_and_stale():
    cfg = _cfg(sms_read_stale_days=3, otp_grace_minutes=5)
    otp = _sms("UPDATE", "59872", "Only use verification code: 749835", unread=False)
    assert await cleanup(FakeTools(), cfg, [otp]) == []   # no first_seen → age unknown → fresh


def test_label_free_reason_covers_only_label_independent_rules():
    cfg = _cfg(sms_read_stale_days=3, sms_completed_patterns=COMPLETED)
    # Old code thread from last year: read + stale, no label needed.
    old = _sms("", "12345", "Citi ID Code: 406808", first_seen=ago(days=330), unread=False)
    assert sms_label_free_reason(old, cfg).startswith("Read notification")
    # Completed alert, no label needed.
    done = _sms("", "(844) 555-0100", "Your package was delivered.", unread=True)
    assert sms_label_free_reason(done, cfg).startswith("Completed delivery")
    # Needs a label → not label-free, so it still goes through the model.
    plain = _sms("", "(844) 555-0101", "We have an offer for you", unread=False)
    assert sms_label_free_reason(plain, cfg) is None
    # A named person is never label-free eligible.
    person = _sms("", "Pat Partner", "Your package was delivered.", unread=False)
    assert sms_label_free_reason(person, cfg) is None


async def test_old_code_thread_from_last_year_is_trashed():
    """The case that prompted this: a year-old verification code, read, never revisited."""
    cfg = _cfg(sms_read_stale_days=3, otp_grace_minutes=5)
    old = _sms("", "Citi", "Citi ID Code: 406808 Onl", first_seen=ago(days=330), unread=False,
               owner_replied=True)
    old.subject = old.id = "12345"          # short code, not a saved contact
    tools = FakeTools()
    log = await cleanup(tools, cfg, [old])
    assert [kw["name"] for _, kw in tools.calls] == ["12345"]
    assert log[0]["reason"].startswith("Read notification")


async def test_unread_notification_goes_once_really_old():
    cfg = _cfg(sms_stale_any_days=30, sms_read_stale_days=3)
    old_unread = _sms("", "85220", "Hi, it's AT&T. Here's the info you requested",
                      first_seen=ago(days=34), unread=True)
    young_unread = _sms("", "85221", "Hi, it's AT&T", first_seen=ago(days=2), unread=True,
                        owner_replied=None)
    tools = FakeTools()
    log = await cleanup(tools, cfg, [old_unread, young_unread])
    assert [kw["name"] for _, kw in tools.calls] == ["85220"]
    assert log[0]["reason"].startswith("Stale notification")


async def test_old_named_thread_goes_only_when_model_says_non_personal():
    cfg = _cfg(sms_named_stale_days=30)
    business = _sms("UPDATE", "Acme Cleaning Co", "Thank you for your business!",
                    first_seen=ago(days=40), unread=True, owner_replied=True)
    promo = _sms("PROMOTION", "Luma", "View on your phone", first_seen=ago(days=40))
    friend = _sms("PERSONAL", "Sam Friend", "Glad she is better!!", first_seen=ago(days=70))
    unsure = _sms("UNSURE", "Chris Neighbour", "Looks like some network issue",
                  first_seen=ago(days=65))
    unlabelled = _sms("", "Dale Carpet Fitter", "thanks", first_seen=ago(days=81))
    recent_promo = _sms("PROMOTION", "Luma2", "View on your phone", first_seen=ago(days=5))
    tools = FakeTools()
    log = await cleanup(tools, cfg, [business, promo, friend, unsure, unlabelled, recent_promo])
    assert [kw["name"] for _, kw in tools.calls] == ["Acme Cleaning Co", "Luma"]
    assert log[0]["reason"].startswith("Non-personal thread")


async def test_listed_personal_contacts_are_never_trashed():
    cfg = _cfg(sms_named_stale_days=30, sms_personal_contacts=["Pat Partner", "Mum"])
    items = [_sms("PROMOTION", "Pat Partner", "sale", first_seen=ago(days=99), unread=False),
             # substring match covers a group thread the person appears in
             _sms("UPDATE", "Realtor, Pat Partner", "fyi", first_seen=ago(days=99)),
             _sms("PROMOTION", "mum", "hi", first_seen=ago(days=99))]
    tools = FakeTools()
    assert await cleanup(tools, cfg, items) == []
    assert tools.calls == []


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
