"""Disk-backed store for files users attach in chat, kept as long as the chat thread, with an audit log.

Per upload, in the upload directory (UPLOAD_DIR):
  <id><ext>        the file itself;
  <id>.meta.json   owner, thread, organizations, name, kind, size, sha256, pages, status;
  audit-YYYY-MM-DD.jsonl   append-only log (uploaded / downloaded / denied / attached / deleted / swept);
  usage-YYYY-MM-DD.json    pages uploaded per user that day, for the daily cap.

Retention (docs/DOCUMENT_UPLOAD_PLAN.md, phase 1): an upload lives as long as its thread. Deleting the thread
deletes its files. An upload never sent with a question is deleted after UNATTACHED_HOURS. The sweep also
deletes the files of threads that no longer exist, so a thread deleted outside the API leaves nothing behind.

Metadata is read from disk on every call, never cached, so several API workers on one host agree. Everything
outside this module goes through the functions below, so S3 can replace the disk without touching callers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.uploads.validate import Inspected, clean_filename

logger = logging.getLogger("gemini_brain.uploads.store")

UNATTACHED_SECONDS = 24 * 3600
ORPHAN_CHECK_SECONDS = 24 * 3600
SWEEP_INTERVAL_SECONDS = 3600
USAGE_KEEP_DAYS = 3

_META_SUFFIX = ".meta.json"
_lock = threading.RLock()
_audit_lock = threading.Lock()
_root: Optional[Path] = None
_last_orphan_check = 0.0
_stop = threading.Event()
_sweeper: Optional[threading.Thread] = None


class DailyLimitReached(Exception):
    """The user already uploaded the day's allowance of pages."""

    def __init__(self, used: int, limit: int):
        super().__init__(f"{used} of {limit} pages used today")
        self.used = used
        self.limit = limit


@dataclass
class UploadRecord:
    id: str
    user_id: int
    session_id: str
    organization_ids: List[int]
    filename: str
    kind: str
    mime: str
    size_bytes: int
    sha256: str
    pages: int
    counted_pages: int
    path: str
    created_at: float = field(default_factory=time.time)
    #: uploaded -> (phase 2) reading -> ready | failed
    status: str = "uploaded"
    failure_reason: str = ""
    #: Set when a question was sent with this file. Until then the upload is a draft that expires.
    attached_at: Optional[float] = None
    #: When the reader started; a read still running long after is a lost one (a restart).
    read_started_at: Optional[float] = None
    #: One line for the chat chip once read, such as "Tax invoice · ACME LLC · AED 525.00".
    summary: str = ""
    #: How many of the code checks on the extract failed.
    checks_failed: int = 0
    #: What the reader decided the file is (tax_invoice, statement, contract, ...); "" until read.
    document_type: str = ""
    #: The checks that failed, each as one plain sentence for the tile ("VAT is 5%. VAT of 52.40 is not 5% of ...").
    problems: List[str] = field(default_factory=list)


def _dir() -> Path:
    global _root
    if _root is None:
        _root = Path(settings.upload_dir) if settings.upload_dir else Path(tempfile.gettempdir()) / "accutax-uploads"
    _root.mkdir(parents=True, exist_ok=True)
    return _root


def _meta_path(upload_id: str) -> Path:
    return _dir() / f"{upload_id}{_META_SUFFIX}"


def _valid_id(upload_id: str) -> bool:
    # The id reaches get() from a URL path; only a real UUID may touch the disk.
    try:
        return str(uuid.UUID(str(upload_id))) == str(upload_id)
    except (ValueError, TypeError):
        return False


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _load(upload_id: str) -> Optional[UploadRecord]:
    if not _valid_id(upload_id):
        return None
    try:
        raw = json.loads(_meta_path(upload_id).read_text(encoding="utf-8"))
        known = set(UploadRecord.__dataclass_fields__)
        return UploadRecord(**{k: v for k, v in raw.items() if k in known})
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("Unreadable upload metadata %s: %s", upload_id, e)
        return None


def _replace(tmp: Path, target: Path) -> None:
    """Atomic: a reader never sees half a file. On Windows a file another thread has open for reading cannot be
    replaced for an instant (PermissionError), so ask again a few times."""
    for attempt in range(6):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.02 * (attempt + 1))


def _write(rec: UploadRecord) -> None:
    tmp = _meta_path(rec.id).with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(rec)), encoding="utf-8")
    _replace(tmp, _meta_path(rec.id))


def _all() -> Iterable[UploadRecord]:
    for meta in _dir().glob(f"*{_META_SUFFIX}"):
        rec = _load(meta.name[: -len(_META_SUFFIX)])
        if rec is not None:
            yield rec


# ── audit log ────────────────────────────────────────────────────────────────

def record_event(
    upload_id: str,
    event: str,
    *,
    user_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> None:
    """Append one audit event. Never raises: auditing must not break an upload or a delete."""
    ts = now if now is not None else time.time()
    entry = {
        "at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "upload_id": upload_id,
        "event": event,
        "user_id": user_id,
        "detail": detail or {},
    }
    try:
        with _audit_lock, open(_dir() / f"audit-{_day(ts)}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Upload audit write failed (%s %s): %s", event, upload_id, e)


def audit_events(upload_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every audit event, oldest first, optionally for one upload."""
    out: List[Dict[str, Any]] = []
    for path in sorted(_dir().glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if upload_id is None or entry.get("upload_id") == upload_id:
                out.append(entry)
    return out


# ── daily page cap ───────────────────────────────────────────────────────────

def _usage_path(ts: float) -> Path:
    return _dir() / f"usage-{_day(ts)}.json"


def _read_usage(ts: float) -> Dict[str, int]:
    try:
        return {str(k): int(v) for k, v in json.loads(_usage_path(ts).read_text(encoding="utf-8")).items()}
    except (FileNotFoundError, ValueError):
        return {}


def pages_used_today(user_id: int, *, now: Optional[float] = None) -> int:
    ts = now if now is not None else time.time()
    with _lock:
        return _read_usage(ts).get(str(int(user_id)), 0)


def _add_usage(user_id: int, pages: int, ts: float) -> None:
    usage = _read_usage(ts)
    key = str(int(user_id))
    usage[key] = max(0, usage.get(key, 0) + pages)
    tmp = _usage_path(ts).with_suffix(".tmp")
    tmp.write_text(json.dumps(usage), encoding="utf-8")
    _replace(tmp, _usage_path(ts))


# ── store ────────────────────────────────────────────────────────────────────

def put(
    content: bytes,
    *,
    filename: str,
    inspected: Inspected,
    user_id: int,
    session_id: str,
    organization_ids: Sequence[int] = (),
    now: Optional[float] = None,
) -> UploadRecord:
    """Store a checked file. Raises DailyLimitReached, leaving nothing on disk, when it would pass the day's cap."""
    ts = now if now is not None else time.time()
    upload_id = str(uuid.uuid4())
    path = _dir() / f"{upload_id}{inspected.ext}"
    rec = UploadRecord(
        id=upload_id,
        user_id=int(user_id),
        session_id=str(session_id),
        organization_ids=sorted({int(o) for o in organization_ids}),
        filename=clean_filename(filename, inspected.kind),
        kind=inspected.kind,
        mime=inspected.mime,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        pages=inspected.pages,
        counted_pages=inspected.counted_pages,
        path=str(path),
        created_at=ts,
    )
    with _lock:  # the cap is checked and the pages added under one lock, so two uploads cannot both squeeze in
        used = _read_usage(ts).get(str(rec.user_id), 0)
        if used + rec.counted_pages > settings.upload_daily_pages:
            raise DailyLimitReached(used, settings.upload_daily_pages)
        path.write_bytes(content)
        _write(rec)
        if rec.counted_pages:
            _add_usage(rec.user_id, rec.counted_pages, ts)
    record_event(rec.id, "uploaded", user_id=rec.user_id, now=ts,
                 detail={"kind": rec.kind, "size_bytes": rec.size_bytes, "pages": rec.pages,
                         "sha256": rec.sha256, "session_id": rec.session_id})
    return rec


def get(upload_id: str) -> Optional[UploadRecord]:
    """The record, or None when unknown or its file is gone."""
    rec = _load(upload_id)
    if rec is None or not os.path.exists(rec.path):
        return None
    return rec


def get_for_user(upload_id: str, user_id: int, *, session_id: Optional[str] = None,
                 action: str = "read") -> Optional[UploadRecord]:
    """The record only if this user owns it (and, when given, it belongs to this thread).

    A refusal returns None, the same as a missing upload, so an id cannot be probed for existence; the
    refusal is written to the audit log.
    """
    rec = get(upload_id)
    if rec is None:
        return None
    if int(rec.user_id) != int(user_id):
        record_event(rec.id, "denied", user_id=user_id, detail={"action": action, "reason": "not_owner"})
        return None
    if session_id is not None and rec.session_id != str(session_id):
        record_event(rec.id, "denied", user_id=user_id, detail={"action": action, "reason": "other_thread"})
        return None
    return rec


def mark_attached(upload_ids: Sequence[str], *, now: Optional[float] = None) -> None:
    """A question was sent with these files: they now live as long as the thread."""
    ts = now if now is not None else time.time()
    with _lock:
        for upload_id in upload_ids:
            rec = _load(upload_id)
            if rec is not None and rec.attached_at is None:
                rec.attached_at = ts
                _write(rec)
                record_event(rec.id, "attached", user_id=rec.user_id, now=ts)


def update(upload_id: str, **changes: Any) -> Optional[UploadRecord]:
    """Change status fields (used by the reader in phase 2)."""
    with _lock:
        rec = _load(upload_id)
        if rec is None:
            return None
        for key, value in changes.items():
            if key not in ("status", "failure_reason", "pages", "counted_pages", "read_started_at", "summary",
                           "checks_failed", "document_type", "problems"):
                raise ValueError(f"upload field {key!r} cannot be changed")
            setattr(rec, key, value)
        _write(rec)
        return rec


def problems_of(rec: UploadRecord) -> List[str]:
    """The tile's list of problems. A file read before this was kept on the record gets it from its stored read."""
    if rec.problems or not rec.checks_failed:
        return list(rec.problems or [])
    from gemini_brain.uploads.checks import problem_sentences

    payload = load_extract(rec.id)
    return problem_sentences((payload or {}).get("checks"))


def read_bytes(rec: UploadRecord) -> bytes:
    return Path(rec.path).read_bytes()


# ── the read of a file (extract, checks, model, tokens), kept beside it ──────

def _extract_path(upload_id: str) -> Path:
    return _dir() / f"{upload_id}.extract.json"


def save_extract(upload_id: str, payload: Dict[str, Any]) -> None:
    tmp = _extract_path(upload_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _replace(tmp, _extract_path(upload_id))


def load_extract(upload_id: str) -> Optional[Dict[str, Any]]:
    if not _valid_id(upload_id):
        return None
    try:
        return json.loads(_extract_path(upload_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def find_cached_extract(user_id: int, sha256: str, version: str, *, exclude_id: str = "") -> Optional[Dict[str, Any]]:
    """The read of the same file (same bytes) by the same user, from a live upload, for this reader version.

    It only looks at uploads that still exist, so a cached read goes when the last thread holding the file does.
    A read made by a model is preferred to a text-only one.
    """
    best: Optional[Dict[str, Any]] = None
    for rec in _all():
        if rec.id == exclude_id or rec.user_id != int(user_id) or rec.sha256 != sha256 or rec.status != "ready":
            continue
        payload = load_extract(rec.id)
        if payload and payload.get("version") == version:
            if best is None or (best.get("source") != "claude" and payload.get("source") == "claude"):
                best = payload
    return best


STALE_READING_SECONDS = 10 * 60  # a read longer than this was lost (a restart); the model call itself times out sooner
LOST_READ = "Reading was interrupted. Attach the file again."


def fail_if_stale(rec: UploadRecord, now: Optional[float] = None) -> UploadRecord:
    """A read still marked `reading` long after it started will never finish: mark it failed."""
    ts = now if now is not None else time.time()
    if rec.status == "reading" and (rec.read_started_at or rec.created_at) + STALE_READING_SECONDS <= ts:
        with _lock:
            fresh = _load(rec.id)
            if fresh is not None and fresh.status == "reading":
                fresh.status, fresh.failure_reason = "failed", LOST_READ
                _write(fresh)
                record_event(fresh.id, "read_failed", user_id=fresh.user_id, detail={"reason": "interrupted"}, now=ts)
                return fresh
    return rec


def fail_stale_reads(now: Optional[float] = None) -> int:
    """Fail every lost read. Returns how many."""
    return sum(1 for rec in list(_all()) if rec.status == "reading" and fail_if_stale(rec, now).status == "failed")


def _remove(rec: UploadRecord) -> None:
    for path in (Path(rec.path), _meta_path(rec.id)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to delete upload file %s: %s", path, e)
    # Anything else keyed on this id (the extract written in phase 2) goes too.
    for extra in _dir().glob(f"{rec.id}.*"):
        try:
            os.remove(extra)
        except Exception:
            pass


def delete(upload_id: str, *, user_id: Optional[int] = None, refund_pages: bool = False,
           reason: str = "deleted", now: Optional[float] = None) -> bool:
    """Delete one upload. `refund_pages` gives its pages back to today's cap (a chip removed before sending)."""
    ts = now if now is not None else time.time()
    with _lock:
        rec = _load(upload_id)
        if rec is None:
            return False
        _remove(rec)
        if refund_pages and rec.counted_pages and _day(rec.created_at) == _day(ts):
            _add_usage(rec.user_id, -rec.counted_pages, ts)
    record_event(rec.id, "deleted", user_id=user_id if user_id is not None else rec.user_id, now=ts,
                 detail={"reason": reason})
    return True


def delete_for_session(session_id: str, *, now: Optional[float] = None) -> int:
    """Delete every upload of a thread. Returns how many were deleted."""
    with _lock:
        ids = [r.id for r in _all() if r.session_id == str(session_id)]
    return sum(1 for i in ids if delete(i, reason="thread_deleted", now=now))


def list_for_session(session_id: str) -> List[UploadRecord]:
    return sorted((r for r in _all() if r.session_id == str(session_id)), key=lambda r: r.created_at)


def disk_usage_bytes() -> int:
    return sum(r.size_bytes for r in _all())


# ── sweep ────────────────────────────────────────────────────────────────────

def sweep(
    now: Optional[float] = None,
    *,
    existing_sessions: Optional[Callable[[List[str]], Optional[set]]] = None,
    check_orphans: bool = False,
) -> Dict[str, int]:
    """Delete drafts past UNATTACHED_SECONDS, old usage files, and (when asked) the files of vanished threads.

    `existing_sessions(ids)` returns the subset of thread ids that still exist, or None when it could not
    tell (the database is down); then no orphan is deleted. Returns counts by reason.
    """
    ts = now if now is not None else time.time()
    counts = {"unattached": 0, "orphaned": 0}
    records = list(_all())
    for rec in records:
        if rec.attached_at is None and rec.created_at + UNATTACHED_SECONDS <= ts:
            if delete(rec.id, reason="unattached_expired", now=ts):
                counts["unattached"] += 1
    if check_orphans and existing_sessions is not None:
        attached = [r for r in records if r.attached_at is not None and os.path.exists(r.path)]
        threads = sorted({r.session_id for r in attached})
        alive = existing_sessions(threads) if threads else set()
        if alive is not None:
            for rec in attached:
                if rec.session_id not in alive and delete(rec.id, reason="thread_missing", now=ts):
                    counts["orphaned"] += 1
    fail_stale_reads(ts)
    cutoff = ts - USAGE_KEEP_DAYS * 86_400
    for usage in _dir().glob("usage-*.json"):
        match = re.fullmatch(r"usage-(\d{4}-\d{2}-\d{2})\.json", usage.name)
        if match and datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() < cutoff:
            try:
                os.remove(usage)
            except Exception:
                pass
    return counts


def _existing_sessions_in_db(ids: List[str]) -> Optional[set]:
    """Thread ids (of `ids`) still in the chat database; None if the database could not be asked."""
    try:
        from gemini_brain.db.connection import get_connection

        conn = get_connection("")
        try:
            cur = conn.cursor()
            cur.execute("SELECT id::text FROM public.model_arena_chat_sessions WHERE id::text = ANY(%s);", (ids,))
            found = {row[0] for row in cur.fetchall()}
            cur.close()
            return found
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Upload orphan check skipped, database not reachable: %s", e)
        return None


def _sweep_loop() -> None:
    global _last_orphan_check
    while not _stop.wait(SWEEP_INTERVAL_SECONDS):
        try:
            due = time.time() - _last_orphan_check >= ORPHAN_CHECK_SECONDS
            sweep(existing_sessions=_existing_sessions_in_db, check_orphans=due)
            if due:
                _last_orphan_check = time.time()
        except Exception as e:
            logger.warning("Upload sweep failed: %s", e)


def start_sweeper() -> None:
    global _sweeper
    if _sweeper and _sweeper.is_alive():
        return
    _stop.clear()
    _sweeper = threading.Thread(target=_sweep_loop, name="upload-sweeper", daemon=True)
    _sweeper.start()


def stop_sweeper() -> None:
    _stop.set()


def reset_for_tests() -> None:
    """Drop all files and counters. Tests only."""
    global _root, _last_orphan_check
    if _root and _root.exists():
        shutil.rmtree(_root, ignore_errors=True)
    _root = None
    _last_orphan_check = 0.0
