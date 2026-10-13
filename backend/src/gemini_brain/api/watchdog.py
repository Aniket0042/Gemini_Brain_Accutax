"""watchdog.py — says in the log why the API stopped answering.

The API once stopped answering every request, /health included, for minutes, with no CPU use and nothing in the
log. Two things can do that: the event loop blocked by a call that waits (a database or network call made directly in
async code), or every thread of the pool that runs the plain endpoints waiting (so none is free for the next
request). This module probes both every few seconds. When one has not answered for `stall_seconds`, it writes every
thread's stack to the log once, so the next stall names the line it is stuck on instead of leaving a guess.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger("gemini_brain.api.watchdog")

PROBE_SECONDS = 2.0
STALL_SECONDS = 10.0


class Watchdog:
    def __init__(self, probe_seconds: float = PROBE_SECONDS, stall_seconds: float = STALL_SECONDS):
        self.probe_seconds = probe_seconds
        self.stall_seconds = stall_seconds
        self.loop_seen = time.monotonic()
        self.pool_seen = time.monotonic()
        #: When the thread pool probe was last sent and not yet answered (None: answered).
        self.pool_sent: Optional[float] = None
        #: When the event loop last came back from a gap. A pool probe cannot be answered while the loop is blocked, so
        #: a pool stall is counted only from this moment.
        self.loop_recovered = 0.0
        self.dumps = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tasks: list = []
        self._dumped_for: Optional[str] = None

    async def _beat_loop(self) -> None:
        while True:
            now = time.monotonic()
            if now - self.loop_seen > self.probe_seconds * 3:
                self.loop_recovered = now
            self.loop_seen = now
            await asyncio.sleep(self.probe_seconds)

    async def _beat_pool(self) -> None:
        from starlette.concurrency import run_in_threadpool

        while True:
            self.pool_sent = time.monotonic()
            await run_in_threadpool(lambda: None)   # needs a free thread of the pool the plain endpoints use
            self.pool_seen = time.monotonic()
            self.pool_sent = None
            await asyncio.sleep(self.probe_seconds)

    def start(self) -> None:
        """Call from the running event loop (the app's lifespan)."""
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._beat_loop()), loop.create_task(self._beat_pool())]
        self._thread = threading.Thread(target=self._watch, name="api-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()

    def _watch(self) -> None:
        while not self._stop.wait(self.probe_seconds):
            self.check()

    def check(self) -> Optional[str]:
        """The stall in progress ('event loop' or 'thread pool'), after dumping the stacks once per stall."""
        now = time.monotonic()
        stalled = None
        if now - self.loop_seen > self.stall_seconds:
            stalled = "event loop"
        elif self.pool_sent is not None and now - max(self.pool_sent, self.loop_recovered) > self.stall_seconds:
            stalled = "thread pool"
        if stalled is None:
            if self._dumped_for:
                logger.warning("API answers again after a stall of the %s.", self._dumped_for)
            self._dumped_for = None
            return None
        if self._dumped_for != stalled:
            self._dumped_for = stalled
            self.dumps += 1
            logger.error("API stalled: the %s has not answered for over %.0f s. Stacks of all threads follow.", stalled,
                         self.stall_seconds)
            try:
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
            except Exception as e:  # noqa: BLE001 - a diagnostic must never take the API down
                logger.error("Could not dump the thread stacks: %s", e)
        return stalled
