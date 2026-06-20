#!/usr/bin/env python3
"""Atheris fuzz harness for the deterministic guard layer.

`garvis/guards.py` decides which mail is *protected from deletion* using fields an attacker
can influence — sender, subject, snippet, the raw date header, and labels. Those functions
must be **total**: if hostile input makes one raise, the protection check around it could be
skipped and Garvis might delete mail it was supposed to keep. This harness drives the guard
functions (regex keyword matching, RFC-2822 / ISO date parsing, OTP detection) with fuzzed
input so Atheris can surface any crashing case.

Run locally:

    pip install -e . atheris          # or: pip install --require-hashes -r requirements-fuzz.txt
    python fuzz/fuzz_guards.py -atheris_runs=100000

Scorecard's Fuzzing check detects this harness via the `import atheris` below.
"""
import sys

import atheris

with atheris.instrument_imports():
    from garvis import guards
    from garvis.config import Config
    from garvis.gather import Item

# Load once: gives the guards real vip_senders / protected_keywords / otp_grace_minutes to
# exercise, so the regex and VIP paths are reached rather than short-circuited.
_CFG = Config.load("config.example.yaml")


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    item = Item(
        source=fdp.ConsumeUnicodeNoSurrogates(8),
        id=fdp.ConsumeUnicodeNoSurrogates(8),
        subject=fdp.ConsumeUnicodeNoSurrogates(120),
        sender=fdp.ConsumeUnicodeNoSurrogates(60),
        date=fdp.ConsumeUnicodeNoSurrogates(40),
        snippet=fdp.ConsumeUnicodeNoSurrogates(200),
        labels=[fdp.ConsumeUnicodeNoSurrogates(12)
                for _ in range(fdp.ConsumeIntInRange(0, 4))],
        has_attachments=fdp.ConsumeBool(),
    )
    # These must never raise on hostile input — that is the security property under test.
    guards.protected_reason(item, _CFG)
    guards.minutes_old(item)
    guards.looks_like_otp(item)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
