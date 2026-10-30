"""scheduler.py — Runs bots on their own, inside the AI service, without the Accutax backend
(docs/VAT_BOT_IMPLEMENTATION_PLAN.md, tasks 2.5 and 3.1 without Nest).

Every BOT_SCHEDULER_INTERVAL_SECONDS a background thread:
1. runs the bots whose next run is due (bots/run.py) and moves each one's next run on;
2. polls for new bills and sales invoices and hands them to the monitor (bots/poller.py).

A Postgres advisory lock wraps each cycle, so two processes (a second worker, an old copy still running) never run
the same bot twice: the one that does not get the lock skips that cycle. Off unless BOT_SCHEDULER_ENABLED is true.
POST /api/v1/bots/tick calls run_due_bots() too, for a scheduler outside this service.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from gemini_brain.config.settings import settings
from gemini_brain.db import connection

logger = logging.getLogger("gemini_brain.bots.scheduler")

#: One tick runs at most this many bots and stops starting new ones after this many seconds; the rest are still due
#: on the next tick.
MAX_BOTS_PER_TICK = 20
TICK_BUDGET_SECONDS = 45.0

#: Key of the Postgres advisory lock for one scheduler cycle (any fixed 64-bit number; "AIBOTS" in hex).
LOCK_KEY = 0x4149424F5453

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def _tz() -> dt.tzinfo:
    return ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc


def next_run_at(draft: Any, subject: str) -> Optional[dt.datetime]:
    """The bot's next run after now, or None when it has no schedule left."""
    from gemini_brain.bots import schedule
    from gemini_brain.vat_return import tool as vat_return_tool
    freqs = vat_return_tool.filing_frequencies(draft.org_ids, subject, time.monotonic() + 5.0) if draft.org_ids else {}
    runs = schedule.next_runs(draft, dt.datetime.now(_tz()), freqs, count=1)
    return runs[0].at if runs else None


def run_due_bots(*, max_bots: int = MAX_BOTS_PER_TICK, budget_seconds: float = TICK_BUDGET_SECONDS) -> List[Dict[str, Any]]:
    """Run the bots that are due now, one after another. A bot that fails is saved as failed and the others carry
    on; every bot's next run moves on, so a failing bot is not due again on every tick."""
    from gemini_brain.bots import store
    from gemini_brain.bots.run import run_bot
    from gemini_brain.bots.schema import BotDraft

    started = time.monotonic()
    results: List[Dict[str, Any]] = []
    for bot in store.due_bots(now=dt.datetime.now(_tz()), limit=max_bots):
        if time.monotonic() - started > budget_seconds:
            break
        bot_id = bot["id"]
        try:
            res = run_bot(bot_id, trigger="schedule")
        except Exception:
            logger.exception("Bot %s run failed", bot_id)
            res = {"status": "failed"}
        try:
            nxt = next_run_at(BotDraft(**bot["draft"]), f"bot-sched:{bot_id}")
        except Exception:
            logger.exception("Could not work out the next run of bot %s", bot_id)
            nxt = None
        try:
            store.set_next_run(bot_id, nxt)
        except Exception:
            logger.exception("Could not save the next run of bot %s", bot_id)
        results.append({"bot_id": bot_id, "name": bot.get("name"), "status": res.get("status", "unknown"),
                        "summary": res.get("summary")})
    return results


def run_cycle() -> Optional[Dict[str, Any]]:
    """One scheduler cycle under the advisory lock. None when another process holds the lock."""
    from gemini_brain.bots import monitor, poller, store

    conn = connection.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
            got = bool(cur.fetchone()[0])
        conn.commit()
        if not got:
            logger.info("Bot scheduler: another process holds the lock; skipping this cycle")
            return None
        try:
            ran = run_due_bots()
            polled: Dict[str, Any] = {}
            try:
                polled = poller.poll(active_org_ids=store.active_org_ids, handle_event=monitor.handle_document_event)
            except Exception:
                logger.exception("Bot monitor poll failed")
            out = {"ran": len(ran), "failed": sum(1 for r in ran if r["status"] not in ("ok", "skipped")), **polled}
            if ran or polled.get("documents"):
                logger.info("Bot scheduler cycle: %s", out)
            return out
        finally:
            # The lock belongs to this database session, and a pooled connection outlives the cycle: always unlock.
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            conn.commit()
    finally:
        conn.close()


def _loop(interval: float) -> None:
    while not _stop.wait(interval):
        try:
            run_cycle()
        except Exception:
            logger.exception("Bot scheduler cycle failed")


def start() -> bool:
    """Start the scheduler thread when BOT_SCHEDULER_ENABLED is on. True if it is running."""
    global _thread
    if not settings.bot_scheduler_enabled:
        return False
    if _thread and _thread.is_alive():
        return True
    _stop.clear()
    interval = max(60, int(settings.bot_scheduler_interval_seconds))
    _thread = threading.Thread(target=_loop, args=(interval,), name="bot-scheduler", daemon=True)
    _thread.start()
    logger.info("Bot scheduler started: every %d seconds", interval)
    return True


def stop() -> None:
    _stop.set()
