# Garvis classification rules

This file is **yours to edit**. Garvis reads it at the start of every run and follows
it. Be specific — the more you tune this, the better the triage.

---

## Protected — NEVER delete, always keep

Even if an item looks like a promotion or update, keep it (and flag it) if ANY apply:

- It is **starred** (Gmail) / **flagged** (Outlook) / marked **important**.
- The sender is on the **VIP list** below.
- The subject or body contains a **protected keyword** below.
- It has an attachment that looks like a document (invoice, contract, statement, PDF,
  spreadsheet, ticket, boarding pass).
- You (the account owner) have **replied** in the thread — it's a real conversation.
- It is a **Garvis digest** (from you@example.com to yourself, subject starts with
  "Garvis digest —"). Never delete your own digests.

### VIP senders (always keep, surface as priority)
<!-- The authoritative list lives in config.yaml (vip_senders) — the code reads that.
     Keep this in sync for humans. -->
- partner@example.com — partner / family
- work@example.com — your work address
- relocation@example.com — a contact you always want surfaced (agent, coordinator, etc.)

### Protected keywords (case-insensitive)
- invoice, statement, refund, chargeback  *(receipts are NOT protected — see below)*
- tax, IRS, W-2, 1099, audit
- legal, contract, agreement, NDA, lawsuit, subpoena
- security alert, suspicious sign-in, password reset
- offer letter, interview, salary, benefits, payroll
- visa, passport, immigration, USCIS
- mortgage, closing, escrow, lease, deposit

> **Receipts** are treated as deletable updates (summarized then deleted), per your
> preference. Bills/**invoices** are still protected.
>
> **One-time / verification codes (OTP, 2FA, MFA)** are protected only while *fresh*.
> After `otp_grace_minutes` (default 5) they are expired noise and become deletable.

---

## Promotions — soft-delete (high confidence only)

Marketing/sales/newsletter mail with no action needed. Signals:

- Bulk marketing footers ("unsubscribe", "view in browser", "manage preferences").
- Sender is a `no-reply@` / `marketing@` / `news@` / `deals@` style address.
- Subject is a sale/discount/promo ("% off", "sale ends", "limited time", "deal").
- Newsletters and digests you don't act on.

### Known promo senders (always treat as promo)
<!-- Add senders/domains you always want cleared. -->
- (add e.g. retailers, newsletters you never read)

---

## Updates — summarize, then soft-delete (high confidence only)

Automated, informational, no reply expected. Summarize the gist into the digest first.

- Shipping/delivery notifications, order confirmations (non-financial-action).
- Social notifications (likes, mentions, follows, connection requests).
- App/service notifications, automated status updates, calendar auto-notices.
- "Your report is ready", "weekly summary", system digests.

> If an "update" contains a real deadline or a thing you must DO, treat it as
> ACTIONABLE instead, not an update.

---

## Actionable / Personal — KEEP

- A real person asking you something or awaiting a reply.
- Anything with a deadline, RSVP, payment due, or decision required.
- Personal correspondence.
- Anything that doesn't clearly fit promo/update → **UNSURE → keep + flag for review.**

## Threads where YOU sent the last message

- **WAITING** — the thread is still open and you're awaiting a reply you want
  (a request, question, or pending decision the other side owes). **Keep**, listed under
  "Waiting on others" — never a to-do for you.
- **CONCLUDED** — your own last message closed it (you declined, said no, or nothing
  further is expected by anyone). **Deletable.** Only when your last message makes that
  clear. VIP-sender threads are always kept regardless.

---

## Aggressiveness dial

- `conservative` — only delete textbook promos/updates; keep more.
- `balanced`  — (default) delete clear promos/updates, keep anything ambiguous.
- `aggressive` — delete anything that isn't clearly actionable/personal.

**Current setting: balanced**
