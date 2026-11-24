"""Keep the VAT knowledge base built without anyone running the CLI.

On a new server the folder is empty: the first check builds it straight away (crawl the FTA
site, download the core PDFs, embed them). After that a build older than VAT_KB_REFRESH_DAYS
is rebuilt, but only in the night hour VAT_KB_REFRESH_HOUR_UTC, because embedding takes a few
minutes of CPU on a small server. The old build keeps serving until the new one is complete
(index.build switches CURRENT last), and the crawler downloads only PDFs it doesn't have yet.

A lock file in the folder stops two API workers from building at once. Nothing here raises
into the API: failures are logged and tried again at the next check.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from gemini_brain.vat_kb.index import CURRENT_FILE, DOCUMENTS_FILE, MODELS_DIR, PDF_DIR, current_build

log = logging.getLogger("gemini_brain.vat_kb.refresh")

LOCK_FILE = ".refresh.lock"
LOCK_STALE_SECONDS = 6 * 3600
CHECK_EVERY_SECONDS = 3600

_thread: threading.Thread | None = None
_stop = threading.Event()


def build_age_days(kb_dir: Path, now: datetime | None = None) -> float | None:
    """Days since the build named in CURRENT was made, or None when there is no usable build."""
    build = current_build(kb_dir)
    if build is None:
        return None
    try:
        made = datetime.strptime(build.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        made = datetime.fromtimestamp((kb_dir / CURRENT_FILE).stat().st_mtime, timezone.utc)
    return ((now or datetime.now(timezone.utc)) - made).total_seconds() / 86400


def due(kb_dir: Path, max_age_days: int, refresh_hour_utc: int, now: datetime | None = None) -> bool:
    """True when there is no build (build now), or the build is too old and it is the night hour."""
    now = now or datetime.now(timezone.utc)
    age = build_age_days(kb_dir, now)
    if age is None:
        return True
    return age >= max_age_days and now.hour == refresh_hour_utc


def _take_lock(kb_dir: Path) -> Path | None:
    lock = kb_dir / LOCK_FILE
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def refresh(kb_dir: Path) -> Path | None:
    """Crawl, download and build once. Returns the new build, or None if another worker is on it."""
    kb_dir.mkdir(parents=True, exist_ok=True)
    lock = _take_lock(kb_dir)
    if lock is None:
        log.info("vat_kb refresh: another process is building, skipped")
        return None
    try:
        from gemini_brain.vat_kb.documents import write_documents
        from gemini_brain.vat_kb.embedder import BgeSmallEmbedder
        from gemini_brain.vat_kb.fta_crawler import collect, download
        from gemini_brain.vat_kb.index import build

        docs = collect()
        write_documents(kb_dir / DOCUMENTS_FILE, docs)
        failures = download(docs, kb_dir / PDF_DIR)
        log.info("vat_kb refresh: %d documents %s, %d downloads failed",
                 len(docs), dict(Counter(d.tier for d in docs)), len(failures))
        # A failed download only leaves that document out; index.build skips missing PDFs.
        return build(kb_dir, BgeSmallEmbedder(kb_dir / MODELS_DIR))
    finally:
        lock.unlink(missing_ok=True)


def _loop(kb_dir: Path, max_age_days: int, refresh_hour_utc: int) -> None:
    while not _stop.is_set():
        try:
            if due(kb_dir, max_age_days, refresh_hour_utc):
                started = time.time()
                built = refresh(kb_dir)
                if built is not None:
                    log.info("vat_kb refresh: %s ready in %d s", built.name, time.time() - started)
        except Exception as e:
            log.warning("vat_kb refresh failed, will try again: %s", e)
        _stop.wait(CHECK_EVERY_SECONDS)


def start(kb_dir: str, enabled: bool, max_age_days: int, refresh_hour_utc: int) -> threading.Thread | None:
    """Start the hourly check. Does nothing when the feature is off, no folder is set, or
    max_age_days is 0 (builds then come only from the CLI, as before)."""
    global _thread
    if not enabled or not kb_dir or max_age_days <= 0:
        return None
    if _thread and _thread.is_alive():
        return _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(Path(kb_dir), max_age_days, refresh_hour_utc),
                               name="vat_kb_refresh", daemon=True)
    _thread.start()
    return _thread


def stop() -> None:
    _stop.set()
