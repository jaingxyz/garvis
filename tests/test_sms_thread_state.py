"""SMS thread-state: did the owner ever reply in an unknown-number thread?"""
from garvis.gather import Item, check_sms_thread_state, is_unnamed_sender


class FakeTools:
    def __init__(self, result=None, raise_exc=None):
        self.result, self.raise_exc, self.calls = result, raise_exc, []

    async def call(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        if self.raise_exc:
            raise self.raise_exc
        return self.result


def _t(name="(555) 010-0000"):
    return Item(source="messages", id=name, subject=name, sender=name, date="", snippet="x")


def test_is_unnamed_sender():
    assert is_unnamed_sender(_t("(555) 010-0000"))
    assert is_unnamed_sender(_t("938763"))
    assert is_unnamed_sender(_t("+1 415-555-0100"))
    assert not is_unnamed_sender(_t("Pat Partner"))
    assert not is_unnamed_sender(_t("Realtor, Pat Partner"))
    mail = Item(source="gmail", id="1", subject="12345", sender="a@b.c", date="", snippet="")
    assert not is_unnamed_sender(mail)


async def test_owner_replied_detected():
    it = _t()
    tools = FakeTools([{"from": "them", "text": "hi"}, {"from": "me", "text": "hello"},
                       {"from": "them", "text": "ok"}])
    await check_sms_thread_state(tools, it)
    assert tools.calls == [("read_conversation", {"name": "(555) 010-0000", "limit": 100})]
    assert it.owner_replied is True
    assert it.owner_replied_last is False and it.last_msg_text == "ok"


async def test_owner_never_replied():
    it = _t()
    await check_sms_thread_state(FakeTools([{"from": "them", "text": "code 1234"}]), it)
    assert it.owner_replied is False and it.owner_replied_last is False


async def test_failure_leaves_state_unknown():
    it = _t()
    await check_sms_thread_state(FakeTools(raise_exc=RuntimeError("browser gone")), it)
    assert it.owner_replied is None
    it = _t()
    await check_sms_thread_state(FakeTools("Error: page.goto failed"), it)
    assert it.owner_replied is None
