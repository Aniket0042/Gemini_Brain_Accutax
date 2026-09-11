"""In-memory + disk store for short-lived downloadable artifacts."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger("gemini_brain.artifacts.store")

TTL_SECONDS = 120
SWEEP_INTERVAL_SECONDS = 15

_lock = threading.Lock()
_records: Dict[str, "ArtifactRecord"] = {}
_expired_ids: Set[str] = set()
_root: Optional[Path] = None
_stop = threading.Event()
_sweeper: Optional[threading.Thread] = None


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


def _dir() -> Path:
    global _root
    if _root is None:
        _root = Path(tempfile.gettempdir()) / "accutax-artifacts"
    _root.mkdir(parents=True, exist_ok=True)
    return _root


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
) -> ArtifactRecord:
    artifact_id = str(uuid.uuid4())
    ext = Path(filename).suffix or ""
    path = _dir() / f"{artifact_id}{ext}"
    path.write_bytes(content)
    rec = ArtifactRecord(
        id=artifact_id,
        user_id=int(user_id),
        organization_id=organization_id,
        session_id=session_id,
        path=str(path),
        mime=mime,
        filename=filename,
        expires_at=(now if now is not None else time.time()) + int(ttl),
    )
    with _lock:
        _records[artifact_id] = rec
        _expired_ids.discard(artifact_id)
    return rec


def _delete_locked(rec: ArtifactRecord) -> None:
    try:
        os.remove(rec.path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("Failed to delete artifact %s: %s", rec.id, e)
    _records.pop(rec.id, None)
    _expired_ids.add(rec.id)


def get(artifact_id: str, *, now: Optional[float] = None) -> Optional[ArtifactRecord]:
    ts = now if now is not None else time.time()
    with _lock:
        rec = _records.get(artifact_id)
        if rec is None:
            return None
        if rec.expires_at <= ts:
            _delete_locked(rec)
            return None
        return rec


def was_expired(artifact_id: str) -> bool:
    with _lock:
        return artifact_id in _expired_ids


def sweep(now: Optional[float] = None) -> int:
    ts = now if now is not None else time.time()
    removed = 0
    with _lock:
        for rec in list(_records.values()):
            if rec.expires_at <= ts:
                _delete_locked(rec)
                removed += 1
    return removed


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
        for rec in list(_records.values()):
            try:
                os.remove(rec.path)
            except FileNotFoundError:
                pass
        _records.clear()
        _expired_ids.clear()
    if _root and _root.exists():
        try:
            shutil.rmtree(_root, ignore_errors=True)
        except Exception:
            pass
