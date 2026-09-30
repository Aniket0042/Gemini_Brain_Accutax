"""Disk-backed store for downloadable report files, with a TTL and an audit log.

Per artifact, in the artifact directory (ARTIFACT_DIR):
  <id><ext>        the file itself — deleted when the TTL runs out;
  <id>.meta.json   owner, organization, expiry, size, sha256, format;
  <id>.spec.json   the validated report spec the file was rendered from —
                   kept for ARTIFACT_SPEC_RETENTION_DAYS after expiry, so an
                   expired link can be regenerated instead of lost.
  audit-YYYY-MM-DD.jsonl  append-only log of created / downloaded / denied /
                   expired / regenerated / purged events, never swept.

The in-memory dict is only a cache over the sidecars: links survive a
restart and resolve from any worker on the same host. Everything outside
this module goes through the functions below, so a Postgres backend can
replace this one without touching callers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.artifacts.store")

TTL_SECONDS = int(settings.artifact_ttl_seconds)
SWEEP_INTERVAL_SECONDS = 60

_META_SUFFIX = ".meta.json"
_SPEC_SUFFIX = ".spec.json"

_lock = threading.Lock()
_audit_lock = threading.Lock()
_records: Dict[str, "ArtifactRecord"] = {}
_root: Optional[Path] = None
_stop = threading.Event()
_sweeper: Optional[threading.Thread] = None


class ArtifactTooLarge(ValueError):
    """A rendered file exceeded ARTIFACT_MAX_BYTES and was not stored."""


@dataclass
class ArtifactRecord:
    id: str
    user_id: int
    organization_id: Optional[int]
    session_id: Optional[str]
    path: str
    mime: str
    filename: str
    expires_at: float
    fmt: str = ""
    size_bytes: int = 0
    sha256: str = ""
    created_at: float = field(default_factory=time.time)
    #: Set when the TTL ran out and the file was deleted; the spec may remain.
    purged_at: Optional[float] = None
    #: Id of the expired artifact this one was regenerated from, if any.
    regenerated_from: Optional[str] = None


def _dir() -> Path:
    global _root
    if _root is None:
        _root = Path(settings.artifact_dir) if settings.artifact_dir else Path(tempfile.gettempdir()) / "accutax-artifacts"
    _root.mkdir(parents=True, exist_ok=True)
    return _root


def _meta_path(artifact_id: str) -> Path:
    return _dir() / f"{artifact_id}{_META_SUFFIX}"


def _spec_path(artifact_id: str) -> Path:
    return _dir() / f"{artifact_id}{_SPEC_SUFFIX}"


def _valid_id(artifact_id: str) -> bool:
    # The id reaches get() from a URL path; only a real UUID may touch the disk.
    try:
        return str(uuid.UUID(str(artifact_id))) == str(artifact_id)
    except (ValueError, TypeError):
        return False


def _load_meta(artifact_id: str) -> Optional[ArtifactRecord]:
    if not _valid_id(artifact_id):
        return None
    try:
        raw = json.loads(_meta_path(artifact_id).read_text(encoding="utf-8"))
        known = {f for f in ArtifactRecord.__dataclass_fields__}
        return ArtifactRecord(**{k: v for k, v in raw.items() if k in known})
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("Unreadable artifact metadata %s: %s", artifact_id, e)
        return None


def _write_meta(rec: ArtifactRecord) -> None:
    tmp = _meta_path(rec.id).with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(rec)), encoding="utf-8")
    os.replace(tmp, _meta_path(rec.id))  # atomic: a reader never sees half a file


# ── audit log ────────────────────────────────────────────────────────────────

def record_event(
    artifact_id: str,
    event: str,
    *,
    user_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> None:
    """Append one audit event. Never raises: auditing must not break a download."""
    ts = now if now is not None else time.time()
    entry = {
        "at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "artifact_id": artifact_id,
        "event": event,
        "user_id": user_id,
        "organization_id": organization_id,
        "detail": detail or {},
    }
    path = _dir() / f"audit-{datetime.fromtimestamp(ts, tz=timezone.utc):%Y-%m-%d}.jsonl"
    try:
        with _audit_lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Artifact audit write failed (%s %s): %s", event, artifact_id, e)


def audit_events(artifact_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every audit event, oldest first, optionally for one artifact."""
    out: List[Dict[str, Any]] = []
    for path in sorted(_dir().glob("audit-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if artifact_id is None or entry.get("artifact_id") == artifact_id:
                out.append(entry)
    return out


# ── store ────────────────────────────────────────────────────────────────────

def put(
    content: bytes,
    *,
    filename: str,
    mime: str,
    user_id: int,
    organization_id: Optional[int] = None,
    session_id: Optional[str] = None,
    ttl: int = TTL_SECONDS,
    now: Optional[float] = None,
    fmt: str = "",
    spec: Optional[Dict[str, Any]] = None,
    regenerated_from: Optional[str] = None,
) -> ArtifactRecord:
    if len(content) > settings.artifact_max_bytes:
        raise ArtifactTooLarge(
            f"{filename} is {len(content):,} bytes; the limit is {settings.artifact_max_bytes:,}"
        )
    ts = now if now is not None else time.time()
    artifact_id = str(uuid.uuid4())
    ext = Path(filename).suffix or ""
    path = _dir() / f"{artifact_id}{ext}"
    path.write_bytes(content)
    if spec is not None:
        _spec_path(artifact_id).write_text(json.dumps(spec, default=str), encoding="utf-8")
    rec = ArtifactRecord(
        id=artifact_id,
        user_id=int(user_id),
        organization_id=organization_id,
        session_id=session_id,
        path=str(path),
        mime=mime,
        filename=filename,
        expires_at=ts + int(ttl),
        fmt=fmt or ext.lstrip("."),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        created_at=ts,
        regenerated_from=regenerated_from,
    )
    _write_meta(rec)
    with _lock:
        _records[artifact_id] = rec
    record_event(artifact_id, "regenerated" if regenerated_from else "created",
                 user_id=rec.user_id, organization_id=organization_id,
                 detail={"fmt": rec.fmt, "size_bytes": rec.size_bytes, "sha256": rec.sha256,
                         **({"from": regenerated_from} if regenerated_from else {})}, now=ts)
    return rec


def _expire_locked(rec: ArtifactRecord, ts: float) -> None:
    """TTL ran out: delete the file, keep metadata (and spec) as a tombstone."""
    try:
        os.remove(rec.path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("Failed to delete artifact file %s: %s", rec.path, e)
    if rec.purged_at is None:
        rec.purged_at = ts
        _write_meta(rec)
        record_event(rec.id, "expired", user_id=rec.user_id, organization_id=rec.organization_id, now=ts)


def get(artifact_id: str, *, now: Optional[float] = None) -> Optional[ArtifactRecord]:
    """The live record, or None when unknown, expired, or its file is gone."""
    ts = now if now is not None else time.time()
    with _lock:
        rec = _records.get(artifact_id) or _load_meta(artifact_id)
        if rec is None:
            return None
        _records[artifact_id] = rec
        if rec.purged_at is not None:
            return None
        if rec.expires_at <= ts:
            _expire_locked(rec, ts)
            return None
        if not os.path.exists(rec.path):
            return None
        return rec


def read_content(rec: ArtifactRecord) -> bytes:
    return Path(rec.path).read_bytes()


def was_expired(artifact_id: str) -> bool:
    """True for a real artifact whose file has expired (survives restarts)."""
    with _lock:
        rec = _records.get(artifact_id) or _load_meta(artifact_id)
    return rec is not None and (rec.purged_at is not None or rec.expires_at <= time.time())


def get_spec(artifact_id: str) -> Optional[Tuple[ArtifactRecord, Dict[str, Any]]]:
    """(record, spec) for regenerating a file — works after the file expired,
    until the spec's retention runs out."""
    rec = _load_meta(artifact_id)
    if rec is None:
        return None
    try:
        spec = json.loads(_spec_path(artifact_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return rec, spec


def sweep(now: Optional[float] = None) -> int:
    """Expire files past their TTL; delete tombstones and specs past retention.

    Returns how many files were expired in this pass.
    """
    ts = now if now is not None else time.time()
    retention = settings.artifact_spec_retention_days * 86_400
    expired = 0
    with _lock:
        for meta in _dir().glob(f"*{_META_SUFFIX}"):
            artifact_id = meta.name[: -len(_META_SUFFIX)]
            rec = _records.get(artifact_id) or _load_meta(artifact_id)
            if rec is None:
                continue
            if rec.purged_at is None and rec.expires_at <= ts:
                _expire_locked(rec, ts)
                expired += 1
            if rec.purged_at is not None and rec.purged_at + retention <= ts:
                for p in (_spec_path(artifact_id), _meta_path(artifact_id)):
                    try:
                        os.remove(p)
                    except FileNotFoundError:
                        pass
                _records.pop(artifact_id, None)
                record_event(artifact_id, "purged", user_id=rec.user_id,
                             organization_id=rec.organization_id, now=ts)
            else:
                _records[artifact_id] = rec
    return expired


def _sweep_loop() -> None:
    while not _stop.wait(SWEEP_INTERVAL_SECONDS):
        try:
            sweep()
        except Exception as e:
            logger.warning("Artifact sweep failed: %s", e)


def start_sweeper() -> None:
    global _sweeper
    if _sweeper and _sweeper.is_alive():
        return
    _stop.clear()
    _sweeper = threading.Thread(target=_sweep_loop, name="artifact-ttl-sweeper", daemon=True)
    _sweeper.start()


def stop_sweeper() -> None:
    _stop.set()


def reset_for_tests() -> None:
    """Drop all records and files. Tests only."""
    with _lock:
        _records.clear()
    if _root and _root.exists():
        try:
            shutil.rmtree(_root, ignore_errors=True)
        except Exception:
            pass
