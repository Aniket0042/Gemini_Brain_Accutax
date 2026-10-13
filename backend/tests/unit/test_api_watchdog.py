"""The watchdog: it notices a stalled event loop or thread pool, and says so once per stall."""
import asyncio
import logging
import time

import pytest

from gemini_brain.api.watchdog import Watchdog


def _run(coro):
    return asyncio.run(coro)


def test_a_healthy_api_is_never_reported():
    async def go():
        w = Watchdog(probe_seconds=0.05, stall_seconds=0.5)
        w.start()
        await asyncio.sleep(0.4)
        result = w.check()
        w.stop()
        return result, w.dumps

    assert _run(go()) == (None, 0)


def test_a_blocked_event_loop_is_reported_once_with_the_stacks(caplog):
    async def go():
        w = Watchdog(probe_seconds=0.05, stall_seconds=0.3)
        w.start()
        await asyncio.sleep(0.2)
        time.sleep(1.0)                      # a call that waits, made on the event loop: nothing else can run
        # while the loop slept the watchdog thread kept running; check it from here as the loop resumes
        reported = w.check()
        again = w.check()
        w.stop()
        return reported, again, w.dumps

    with caplog.at_level(logging.WARNING, logger="gemini_brain.api.watchdog"):
        reported, again, dumps = _run(go())
    assert reported == "event loop" and again == "event loop" and dumps == 1     # stacks dumped once, not on every probe
    assert any("API stalled" in r.message for r in caplog.records)


def test_a_stall_that_ends_is_logged_and_can_be_reported_again(caplog):
    w = Watchdog(probe_seconds=0.05, stall_seconds=0.1)
    w.loop_seen = w.pool_seen = time.monotonic() - 1
    with caplog.at_level(logging.WARNING, logger="gemini_brain.api.watchdog"):
        assert w.check() == "event loop"
        w.loop_seen = w.pool_seen = time.monotonic()
        assert w.check() is None
        w.loop_seen = w.pool_seen = time.monotonic() - 1
        assert w.check() == "event loop"
    assert w.dumps == 2 and any("answers again" in r.message for r in caplog.records)


def test_a_full_thread_pool_is_told_apart_from_a_blocked_loop():
    w = Watchdog(probe_seconds=0.05, stall_seconds=0.1)
    w.loop_seen = time.monotonic()
    w.pool_sent = time.monotonic() - 1          # the probe went out a second ago and has not come back
    assert w.check() == "thread pool"


def test_a_pool_probe_held_up_by_a_blocked_loop_is_not_a_pool_stall():
    w = Watchdog(probe_seconds=0.05, stall_seconds=0.1)
    w.pool_sent = time.monotonic() - 1
    w.loop_recovered = time.monotonic()         # the loop has only just come back
    w.loop_seen = time.monotonic()
    assert w.check() is None
