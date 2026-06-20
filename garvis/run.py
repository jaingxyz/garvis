"""Garvis orchestrator: gather -> thread-state -> classify -> prioritize -> clean -> digest.

Usage:
    python -m garvis.run                 # uses config.yaml (dry_run respected)
    python -m garvis.run --no-email      # skip emailing the digest
    python -m garvis.run --status        # print latest digest (no scan)
    python -m garvis.run --delta         # fast recent-only sweep (quick back-and-forth)
    python -m garvis.run --continuous    # background daemon: loop delta sweeps (pre-compute)
    python -m garvis.run --check         # connect + list MCP tools + ping LLM, then exit
    GARVIS_DRY_RUN=true python -m garvis.run   # force for this invocation (used by MCP)
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
from datetime import datetime, timezone

from . import classify as C
from . import digest as D
from . import gather as G
from . import prioritize as P
from .actions import cleanup
from .config import Config
from .llm import build_llm
from .mcp_client import connect
from .store import Store


async def check(cfg: Config) -> None:
    tools = await connect(cfg)
    print("MCP tools available:")
    for n in tools.names():
        print(f"  - {n}")
    llm = build_llm(cfg)
    reply = await llm.ainvoke([("human", "Reply with the single word: ready")])
    print(f"\nLLM ({cfg.llm['model']}) says: {reply.content!r}")


def _clear_messages_browser() -> None:
    """Kill any stray Chromium holding the google-messages profile lock.

    The Google Messages MCP drives one shared Chromium profile; a leftover instance
    (from a prior run or another client) makes texts silently unavailable. Clearing it
    before we connect guarantees this run can claim the profile.

    This is skipped when GM_HEADLESS is enabled (experimental), because a headless
    instance should not contend for a visible window in the same way.
    """
    try:
        r = subprocess.run(
            ["pkill", "-f", "google-messages-mcp/profile"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("[garvis] cleared a stray google-messages Chromium")
    except FileNotFoundError:
        pass  # no pkill (non-macOS/Linux) — nothing to do


async def run(cfg: Config, send_email: bool = True, *, lookback_minutes: int | None = None) -> None:
    # Support fire-and-forget calls from the Garvis MCP server (garvis_run_sweep tool).
    # The MCP handler launches us in a subprocess and sets GARVIS_DRY_RUN so the
    # long sweep does not block the stdio transport.
    import os
    if "GARVIS_DRY_RUN" in os.environ:
        val = os.environ["GARVIS_DRY_RUN"].lower() in ("1", "true", "yes", "on")
        cfg.raw["dry_run"] = val

    ts = datetime.now(timezone.utc).astimezone()
    texts_spec = cfg.raw.get("mcp_servers", {}).get("google-messages") or {}
    texts_enabled = texts_spec.get("enabled", True) if isinstance(texts_spec, dict) else True
    env = texts_spec.get("env") if isinstance(texts_spec, dict) else {}
    env = env or {}
    texts_headless = str(env.get("GM_HEADLESS", "")).lower() in (
        "true",
        "1",
    )
    if texts_enabled and not texts_headless:
        _clear_messages_browser()
        await asyncio.sleep(2)  # let the OS release the profile lock before relaunch
    whatsapp_spec = cfg.raw.get("mcp_servers", {}).get("whatsapp") or {}
    whatsapp_enabled = (
        whatsapp_spec.get("enabled", True) if isinstance(whatsapp_spec, dict) else True
    )
    tools = await connect(cfg)
    text_llm = build_llm(cfg)          # free-text for prioritize briefing
    json_llm = build_llm(cfg, format="json")  # structured for classify (Ollama + retries)
    rules = cfg.read_text("rules")
    store = Store(cfg.path("db"))
    run_id = store.start_run("dry-run" if cfg.dry_run else "live")

    # memory graph: load the user's profile (people + facts) and render a context block
    # that the classifier + prioritizer use to judge mail in light of who's who.
    profile_path = cfg.root / "config" / "profile.yaml"
    if profile_path.exists():
        import yaml
        store.sync_profile(yaml.safe_load(profile_path.read_text()) or {})
    profile_ctx = store.profile_context()

    # 1. gather
    print("[garvis] starting gather phase (gmail/outlook/texts/whatsapp via MCPs)...")
    gmail = await G.gather_gmail(tools, cfg, lookback_minutes=lookback_minutes)
    outlook = await G.gather_outlook(tools, cfg, lookback_minutes=lookback_minutes)
    texts = await G.gather_messages(tools, cfg) if texts_enabled else []
    whatsapp = await G.gather_whatsapp(tools, cfg, lookback_minutes=lookback_minutes) if whatsapp_enabled else []
    items = G.dedupe_threads(gmail + outlook + texts + whatsapp)
    texts_ok = texts_enabled and len(texts) > 0
    print(f"[garvis] gathered {len(gmail)+len(outlook)+len(texts)+len(whatsapp)} messages "
          f"(gmail={len(gmail)} outlook={len(outlook)} texts={len(texts)} whatsapp={len(whatsapp)}"
          f"{'' if texts_enabled else ', texts disabled'}) "
          f"-> {len(items)} threads after dedupe")
    print("[garvis] gather complete. Now thread-state checks + classify (LLM thinking)...")

    # 2. thread-state (only worth it for plausibly-conversational mail)
    for it in items:
        if it.source in ("gmail", "outlook") and "Re:" in (it.subject or ""):
            await G.check_thread_state(tools, cfg, it)

    # 3. classify (use json-forced llm for reliable structured output)
    print("[garvis] classifying items (watch for [garvis thinking] LLM logs below)...")
    for it in items:
        await C.classify_item(json_llm, cfg, rules, it, profile_ctx)
        print(f"  [{it.label:10}] {it.source}: {it.subject[:60]}")

    # 4. prioritize into a chief-of-staff briefing (free text)
    actionable = [i for i in items if i.label in ("ACTIONABLE", "PERSONAL")]
    waiting = [i for i in items if i.label == "WAITING"]
    print("[garvis] prioritizing with LLM (watch thinking logs)...")
    priorities = await P.prioritize(text_llm, actionable, waiting,
                                    f"{ts:%A, %B %d, %Y}", profile_ctx)

    # 5. cleanup (dry-run aware)
    log = await cleanup(tools, cfg, items)

    # 6. digest
    md = D.render(cfg, ts, priorities, items, log, texts_ok)
    fp = D.write_file(cfg, ts, md)
    print(f"[garvis] digest written: {fp}")
    if send_email:
        try:
            await D.email_copy(tools, cfg, ts, md)
            print("[garvis] digest emailed")
        except Exception as e:  # noqa: BLE001
            print(f"[garvis] email failed: {e}")

    # 7. persist run + audit + durable state (entities + sticky loops)
    store.record_actions(run_id, log)
    store.mark_seen(run_id, items)
    store.seed_entities(cfg.raw.get("vip_senders", []) or [])
    store.sync_loops(run_id, items)
    flagged = sum(1 for i in items if i.label == "UNSURE")
    store.finish_run(run_id, scanned=len(gmail) + len(outlook) + len(texts),
                     threads=len(items), deleted=sum(1 for e in log if e.get("performed")),
                     flagged=flagged)

    cfg.write_state(ts.isoformat())
    print(f"[garvis] done. run #{run_id}, dry_run={cfg.dry_run}, "
          f"{len(log)} cleanup entries logged to {cfg.path('db')}")


def show_history(cfg: Config, n: int = 10) -> None:
    store = Store(cfg.path("db"))
    print("=== recent runs ===")
    for r in store.recent_runs(n):
        when = (r["started_at"] or "")[:19]
        print(f"  #{r['id']:>3} {when}  {r['mode']:<8} "
              f"scanned={r['scanned']} threads={r['threads']} "
              f"deleted={r['deleted']} flagged={r['flagged']}")
    print("\n=== recent cleanup actions ===")
    for a in store.recent_actions(n * 2):
        status = "DELETED" if a["performed"] else ("dry-run" if a["dry_run"] else "—")
        print(f"  [{status:7}] {a['account']:7} {a['decision']:10} "
              f"{(a['subject'] or '')[:50]}  ({a['message_id'][:14]}…)")


def show_status(cfg: Config) -> None:
    """Print the most recent digest without running a new sweep."""
    digests_dir = cfg.path("digests")
    digests = sorted(digests_dir.glob("20*.md"))
    if not digests:
        print("No digests yet. Run a sweep first.")
        return
    latest = digests[-1]
    print(f"=== {latest.name} ===")
    print(latest.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(prog="garvis")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--check", action="store_true", help="connectivity check only")
    ap.add_argument("--history", action="store_true", help="show run history + audit log")
    ap.add_argument("--status", action="store_true", help="print the latest digest (no scan)")
    ap.add_argument("--delta", action="store_true", help="fast delta sweep (last ~15-30 min only, for quick back-and-forth)")
    ap.add_argument("--continuous", action="store_true", help="background daemon: keep pulling deltas + processing with LLM so voice/requests are fast (pre-computed + deltas)")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.history:
        show_history(cfg)
        return
    if args.status:
        show_status(cfg)
        return
    if args.continuous:
        asyncio.run(continuous_worker(cfg))
        return
    if args.check:
        asyncio.run(check(cfg))
    else:
        lb = 20 if args.delta else None
        asyncio.run(run(cfg, send_email=not args.no_email, lookback_minutes=lb))


async def continuous_worker(cfg: Config) -> None:
    """Background worker for fast back-and-forth.
    Periodically does small delta pulls + LLM processing.
    Voice / --status / TUI can then rely on pre-computed loops + digest and only
    request deltas on explicit 'update'.
    """
    print("[garvis continuous] starting background worker (delta every 45s)...")
    while True:
        try:
            # Do a fast delta sweep (ingest + classify on recent only)
            await run(cfg, send_email=False, lookback_minutes=20)
            print("[garvis continuous] delta cycle done. Sleeping 45s...")
        except Exception as e:
            print(f"[garvis continuous] cycle error (ignored): {e}")
        await asyncio.sleep(45)


if __name__ == "__main__":
    main()
