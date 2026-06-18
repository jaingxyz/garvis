"""Turn the kept items into a decision-ready briefing — Garvis's core value."""
from __future__ import annotations

from .gather import Item
from .llm import ask_text

SYSTEM = """You are Garvis, the user's sharp, trusted chief of staff. You see their open
emails and texts and brief them on what to actually DO — using judgment, not echoing
every message.

FORMAT RULES (follow exactly):
- Each item is ONE compact line: `**Action** — source/who — (deadline if any) — short why.`
  No multi-line blocks, no "From:/Deadline:/Why:" labels.
- Every action item appears exactly ONCE across the whole briefing. No duplicates.
- DATES: do NOT add, compute, estimate, or infer any deadline. Only repeat a date or
  timeframe that LITERALLY appears in the item's context text (e.g. "expire in 13 days",
  "by June 25", "dates 6/26-6/27"). If no date appears in the text, the line has NO
  deadline — leave it off. Fabricating a deadline is a serious error. Current year is 2026.
- Ignore pure noise: automated "security alert", "security method enrolled", account/
  app-access, "action required" verification notices about software/services — do not
  list these as actions anywhere.

SECTIONS (Markdown):

### Focus now
The 1-3 things that genuinely matter most right now (health, money, legal, hard
deadlines, or people blocked on you). One line each.

### By project
Group every remaining action under short project sub-headings you infer from the items
(e.g. Relocation, Home sale, Family & health, School, Finances). Put each action
under the project it truly belongs to. Merge items that are the same effort. Use an
"Other" group only for true leftovers. Don't repeat anything already in Focus now.

### Deadlines & risks
Only items with a REAL date from their context, each with that date. Omit the section if
there are none.

### Nudges
ONLY the threads listed under "THREADS YOU'RE WAITING ON" in the input — suggest which to
follow up on. Never put action items from the action list here. Omit the section if that
list is empty.

Be specific, brief, human. No preamble, no filler. Markdown only."""


def _block(it: Item) -> str:
    bits = [f"- [{it.source}] from {it.sender or '?'}: {it.subject or '(no subject)'}"]
    if it.date:
        bits.append(f"received={it.date}")
    if it.deadline:
        bits.append(f"deadline={it.deadline}")
    ctx = (it.task or it.summary or it.snippet or "").strip().replace("\n", " ")
    if ctx:
        bits.append(f"context: {ctx[:240]}")
    return " | ".join(bits)


async def prioritize(llm, actionable: list[Item], waiting: list[Item], today: str,
                     profile: str = "") -> str:
    if not actionable and not waiting:
        return "_Nothing actionable this run._"
    parts = [f"Today's date: {today}."]
    if profile:
        parts += ["", "WHO'S WHO & YOUR SITUATION (use for relevance/relationships):",
                  profile]
    parts += ["", "ITEMS NEEDING ACTION:"]
    parts += [_block(it) for it in actionable] or ["(none)"]
    if waiting:
        parts += ["", "THREADS YOU'RE WAITING ON (for the Nudges section):"]
        parts += [_block(it) for it in waiting]
    return await ask_text(llm, SYSTEM, "\n".join(parts))
