"""LLM classification of each gathered item, governed by config/rules.md."""
from __future__ import annotations

from .config import Config
from .gather import Item
from .guards import otp_is_deletable, protected_reason
from .llm import ask_json

SYSTEM = """You are Garvis, an email/message triage classifier. You follow the user's
rules exactly. Classify a single item into one of:
- PROMOTION: marketing/newsletter/sales, no action needed.
- UPDATE: automated/informational notification (shipping, receipts you don't act on,
  social, status). Provide a one-line summary.
- ACTIONABLE: someone is asking the owner for something, or there is a deadline the
  owner must act on. Provide the task and any deadline.
- PERSONAL: real personal correspondence to keep.
- WAITING: a real, still-open thread where the OWNER sent the latest reply and is
  awaiting a response they want (e.g. a request, question, or pending decision the other
  party still owes). Keep it. Not a task for the owner.
- CONCLUDED: a thread the OWNER sent the last message on that is FINISHED — they
  declined/closed it, said no, or no further action is expected by anyone. Deletable.
  Only use this when the owner's own last message makes clear the matter is done.
- UNSURE: anything you cannot confidently place -> keep.

Protected items (NEVER mark PROMOTION/UPDATE; prefer PERSONAL/ACTIONABLE/UNSURE):
starred/flagged/important mail, VIP senders, protected keywords (invoice, receipt, tax,
legal, contract, security/verification code/2FA/password, payroll, visa, mortgage),
document attachments, and Garvis's own digests.

Respond with ONLY a JSON object:
{"label": "...", "reason": "...", "summary": "...", "task": "...", "deadline": ""}"""


def _user_prompt(item: Item, rules: str) -> str:
    owner_state = {
        True: "OWNER sent the latest message (choose WAITING if still open, "
              "CONCLUDED if the owner's last message closed it).",
        False: "Someone else sent the latest message (may be ACTIONABLE).",
        None: "Thread state unknown.",
    }[item.owner_replied_last]
    tail = ""
    if item.last_msg_from:
        tail = (f"latest_message_from: {item.last_msg_from}\n"
                f"latest_message_text: {item.last_msg_text[:400]}\n")
    return (
        f"=== USER RULES ===\n{rules}\n\n"
        f"=== ITEM ===\n"
        f"source: {item.source}\n"
        f"from: {item.sender}\n"
        f"subject: {item.subject}\n"
        f"gmail_labels: {item.labels}\n"
        f"thread_state: {owner_state}\n"
        f"{tail}"
        f"snippet: {item.snippet[:600]}\n"
    )


async def classify_item(llm, cfg: Config, rules: str, item: Item,
                        profile: str = "") -> Item:
    # llm passed in; for JSON steps the caller can supply one built with format="json"
    # (see run.py). ask_json provides the retry/nudge/defensive logic regardless.
    user = _user_prompt(item, rules)
    if profile:
        user = f"=== WHO'S WHO (memory) ===\n{profile}\n\n{user}"
    data = await ask_json(llm, SYSTEM, user)
    item.label = (data.get("label") or "UNSURE").upper().strip()
    item.reason = data.get("reason", "")
    item.summary = data.get("summary", "")
    item.task = data.get("task", "")
    item.deadline = data.get("deadline", "")
    # Hard guard: if the owner spoke last, it's never a fresh task for them.
    if item.owner_replied_last and item.label == "ACTIONABLE":
        item.label = "WAITING"
        item.reason = "Owner already sent the latest reply; " + item.reason
    # CONCLUDED is only valid when the owner actually sent the last message. If the model
    # marked something CONCLUDED without that, keep it (downgrade to UNSURE) to be safe.
    if item.label == "CONCLUDED" and not item.owner_replied_last:
        item.label = "UNSURE"
        item.reason = "Marked concluded but owner did not reply last; kept. " + item.reason
    # Expired one-time codes are noise -> make them deletable updates.
    if otp_is_deletable(item, cfg):
        item.label = "UPDATE"
        item.summary = item.summary or "Expired one-time/verification code"
        item.reason = "One-time code past grace window; safe to delete. " + item.reason
    # Hard guard: protected items can never be deletable, whatever the LLM said.
    pr = protected_reason(item, cfg)
    if pr and item.label in ("PROMOTION", "UPDATE", "CONCLUDED"):
        item.label = "UNSURE"
        item.reason = f"protected ({pr}); kept for review. " + item.reason
    return item
