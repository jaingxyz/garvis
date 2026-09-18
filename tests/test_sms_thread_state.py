"""SMS thread-state: did the owner ever reply in an unknown-number thread?"""
from conftest import FakeTools, make_cfg

from garvis.gather import (
    SMS_THREAD_READ_LIMIT,
    Item,
    check_sms_thread_state,
    is_unnamed_sender,
    notification_match,
)


def _t(name="(555) 010-0000", snippet="x"):
    return Item(source="messages", id=name, subject=name, sender=name, date="", snippet=snippet)


def test_is_unnamed_sender():
    assert is_unnamed_sender(_t("(555) 010-0000"))
    assert is_unnamed_sender(_t("938763"))
    assert is_unnamed_sender(_t("+1 415-555-0100"))
    assert not is_unnamed_sender(_t("Pat Partner"))
    assert not is_unnamed_sender(_t("Realtor, Pat Partner"))
    assert not is_unnamed_sender(_t(""))          # scrape missed the name: never act on it
    assert not is_unnamed_sender(_t("   "))
    mail = Item(source="gmail", id="1", subject="12345", sender="a@b.c", date="", snippet="")
    assert not is_unnamed_sender(mail)


def test_notification_match():
    cfg = make_cfg(sms_notification_senders=["Carrier Alerts"],
                   sms_notification_patterns=[r"voice ?mail", "("])   # "(" is a bad regex
    assert notification_match(_t("12345"), cfg) == "unknown number"
    assert notification_match(_t("carrier alerts"), cfg) == "notification sender (Carrier Alerts)"
    assert notification_match(_t("Owner Self", "New Voicemail from 555"), cfg) == \
        "notification pattern (voice ?mail)"
    assert notification_match(_t("Owner Self", "lunch?"), cfg) is None
    assert notification_match(_t("Pat Partner"), make_cfg()) is None


async def test_owner_replied_detected():
    it = _t()
    tools = FakeTools([{"from": "them", "text": "hi"}, {"from": "me", "text": "hello"},
                       {"from": "them", "text": "ok"}])
    await check_sms_thread_state(tools, it)
    assert tools.calls == [("read_conversation", {"name": "(555) 010-0000", "limit": SMS_THREAD_READ_LIMIT})]
    assert it.owner_replied is True
    assert it.owner_replied_last is False and it.last_msg_text == "ok"


async def test_owner_never_replied():
    it = _t()
    await check_sms_thread_state(FakeTools([{"from": "them", "text": "code 1234"}]), it)
    assert it.owner_replied is False and it.owner_replied_last is False


async def test_inconclusive_reads_leave_state_unknown():
    # Failure, error text, an empty read (selectors matched nothing) and a full page with no
    # owner message (a reply could be older than the page) all mean "unknown" → keep.
    for tools in (FakeTools(raise_exc=RuntimeError("browser gone")),
                  FakeTools("Error: page.goto failed"),
                  FakeTools([]),
                  FakeTools([{"from": "them", "text": "alert"}] * SMS_THREAD_READ_LIMIT)):
        it = _t()
        await check_sms_thread_state(tools, it)
        assert it.owner_replied is None
    # A full page that does contain an owner message is conclusive.
    it = _t()
    full = [{"from": "them", "text": "alert"}] * (SMS_THREAD_READ_LIMIT - 1) + [{"from": "me", "text": "STOP"}]
    await check_sms_thread_state(FakeTools(full), it)
    assert it.owner_replied is True
