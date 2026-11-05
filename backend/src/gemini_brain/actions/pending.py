"""pending.py: drafts the assistant prepared and the person has not confirmed yet, one JSON file each, on disk.

Why disk: no database change is needed (the AI's database role is read-only and schema changes go through the DBA),
and every API worker on one host sees the same files. Everything outside this module goes through the functions
below, so a table or Redis can replace the disk later without touching callers.

Life of an action:
    pending ──update()──> pending
    pending ──claim()───> confirming ──finish()──> done
                                     ──fail(retry=True)──> pending   (Accutax refused it; nothing was written)
                                     ──fail(retry=False)─> failed    (outcome unknown, e.g. a timed-out create)
    pending ──discard()─> discarded
    any state but done, once older than PENDING_ACTION_TTL_HOURS, counts as expired and is swept.

claim() is the guard against double writes: it moves pending to confirming under a per-action lock, so two clicks,
two tabs or two workers confirm one action at most once. The payload to write always comes from this store, never
from the confirm request.

An action belongs to one person and one organization (and the chat it was made in). get_for() returns None for
anyone else, so a guessed id reveals nothing.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.actions.pending")

PENDING, CONFIRMING, DONE, FAILED, DISCARDED = "pending", "confirming", "done", "failed", "discarded"
STATUSES = frozenset({PENDING, CONFIRMING, DONE, FAILED, DISCARDED})
_ID = re.compile(r"^act_[0-9a-f]{32}$")
_LOCK_STALE_SECONDS = 30.0
_LOCK_WAIT_SECONDS = 5.0


class ActionStateError(Exception):
    """The action is not in a state that allows this step (already confirmed, discarded, expired...)."""

    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


@dataclass
class PendingAction:
    id: str
    kind: str                       # e.g. "sales_document"
    user_id: int
    organization_id: int
    session_id: Optional[str]
    payload: Dict[str, Any]         # what confirm will send to Accutax, built in code
    view: Dict[str, Any] = field(default_factory=dict)    # what the card shows (names, totals), built in code
    status: str = PENDING
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0
    version: int = 1
    result: Dict[str, Any] = field(default_factory=dict)  # Accutax record id and number once done
    error: Dict[str, Any] = field(default_factory=dict)   # {"kind", "message"} of the last failure

    def expired(self, now: Optional[float] = None) -> bool:
        return self.status != DONE and (now or time.time()) >= self.expires_at

    def public(self) -> Dict[str, Any]:
        """What the chat UI may see: never the user id or the raw payload."""
        return {"id": self.id, "kind": self.kind, "organization_id": self.organization_id,
                "status": "expired" if self.expired() else self.status, "version": self.version,
                "view": self.view, "result": self.result, "error": self.error, "expires_at": self.expires_at}


_root: Optional[Path] = None


def _dir() -> Path:
    global _root
    if _root is None:
        raw = settings.pending_action_dir
        _root = Path(raw) if raw else Path(tempfile.gettempdir()) / "accutax-pending-actions"
    _root.mkdir(parents=True, exist_ok=True)
    return _root


def reset_dir_cache() -> None:
    """For tests that point PENDING_ACTION_DIR somewhere else."""
    global _root
    _root = None


def valid_id(action_id: str) -> bool:
    return bool(_ID.match(str(action_id or "")))


def _path(action_id: str) -> Path:
    return _dir() / f"{action_id}.json"


@contextlib.contextmanager
def _locked(action_id: str) -> Iterator[None]:
    """A lock across threads and processes on one host: an exclusively created <id>.lock file."""
    lock = _dir() / f"{action_id}.lock"
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock.unlink(missing_ok=True)  # a worker died holding it
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ActionStateError("busy", "This draft is being changed right now; try again.")
            time.sleep(0.02)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _load(action_id: str) -> Optional[PendingAction]:
    if not valid_id(action_id):
        return None
    try:
        data = json.loads(_path(action_id).read_text(encoding="utf-8"))
        return PendingAction(**data)
    except FileNotFoundError:
        return None
    except (ValueError, TypeError) as e:
        logger.warning("pending action %s unreadable: %s", action_id, e)
        return None


def _write(action: PendingAction) -> None:
    target = _path(action.id)
    fd, tmp = tempfile.mkstemp(dir=str(_dir()), prefix=f".{action.id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(action), fh, ensure_ascii=False, default=str)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def create(kind: str, *, user_id: int, organization_id: int, session_id: Optional[str],
           payload: Dict[str, Any], view: Optional[Dict[str, Any]] = None,
           now: Optional[float] = None) -> PendingAction:
    ts = now or time.time()
    action = PendingAction(id=f"act_{uuid.uuid4().hex}", kind=kind, user_id=int(user_id),
                           organization_id=int(organization_id), session_id=session_id, payload=payload,
                           view=view or {}, created_at=ts, updated_at=ts,
                           expires_at=ts + settings.pending_action_ttl_hours * 3600)
    _write(action)
    return action


def get_for(action_id: str, *, user_id: int, organization_id: Optional[int] = None) -> Optional[PendingAction]:
    """The action, only for its owner (and its organization when given). None for anyone else or a bad id."""
    action = _load(action_id)
    if action is None or action.user_id != int(user_id):
        return None
    if organization_id is not None and action.organization_id != int(organization_id):
        return None
    return action


def _owned(action_id: str, user_id: int) -> PendingAction:
    action = get_for(action_id, user_id=user_id)
    if action is None:
        raise ActionStateError("not_found", "That draft was not found.")
    return action


def _require_open(action: PendingAction, now: Optional[float] = None) -> None:
    if action.expired(now):
        raise ActionStateError("expired", "This draft has expired. Ask again to prepare a new one.")
    if action.status != PENDING:
        messages = {CONFIRMING: "This draft is being saved right now.", DONE: "This draft was already saved.",
                    FAILED: "Saving this draft did not finish; check Accutax before trying again.",
                    DISCARDED: "This draft was discarded."}
        raise ActionStateError(action.status, messages.get(action.status, "This draft cannot be changed."))


def update(action_id: str, *, user_id: int, payload: Dict[str, Any], view: Optional[Dict[str, Any]] = None,
           expected_version: Optional[int] = None, now: Optional[float] = None) -> PendingAction:
    """Replace the payload (and view) of a pending action. `expected_version` refuses a stale edit."""
    with _locked(action_id):
        action = _owned(action_id, user_id)
        _require_open(action, now)
        if expected_version is not None and action.version != expected_version:
            raise ActionStateError("stale", "This draft changed since you saw it; look at the latest card.")
        action.payload = payload
        if view is not None:
            action.view = view
        action.version += 1
        action.updated_at = now or time.time()
        _write(action)
        return action


def claim(action_id: str, *, user_id: int, organization_id: int,
          expected_version: Optional[int] = None, now: Optional[float] = None) -> PendingAction:
    """pending -> confirming, at most once. The caller then writes to Accutax and calls finish() or fail()."""
    with _locked(action_id):
        action = _owned(action_id, user_id)
        if action.organization_id != int(organization_id):
            raise ActionStateError("not_found", "That draft was not found.")
        _require_open(action, now)
        if expected_version is not None and action.version != expected_version:
            raise ActionStateError("stale", "This draft changed since you saw it; look at the latest card.")
        action.status = CONFIRMING
        action.error = {}
        action.updated_at = now or time.time()
        _write(action)
        return action


def finish(action_id: str, *, result: Dict[str, Any], now: Optional[float] = None) -> PendingAction:
    """confirming -> done, with the Accutax record (id, number...)."""
    return _settle(action_id, DONE, result=result, now=now)


def fail(action_id: str, *, kind: str, message: str, retry: bool, now: Optional[float] = None) -> PendingAction:
    """confirming -> pending (retry=True: Accutax refused, nothing written) or failed (outcome unknown)."""
    return _settle(action_id, PENDING if retry else FAILED, error={"kind": kind, "message": message}, now=now)


def _settle(action_id: str, status: str, *, result: Optional[Dict[str, Any]] = None,
            error: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> PendingAction:
    with _locked(action_id):
        action = _load(action_id)
        if action is None:
            raise ActionStateError("not_found", "That draft was not found.")
        if action.status != CONFIRMING:
            raise ActionStateError(action.status, "This draft is not being saved.")
        action.status = status
        if result is not None:
            action.result = result
        if error is not None:
            action.error = error
        action.updated_at = now or time.time()
        _write(action)
        return action


def discard(action_id: str, *, user_id: int, now: Optional[float] = None) -> PendingAction:
    with _locked(action_id):
        action = _owned(action_id, user_id)
        if action.status == DISCARDED:
            return action
        _require_open(action, now)
        action.status = DISCARDED
        action.updated_at = now or time.time()
        _write(action)
        return action


def latest_in_chat(kinds: Sequence[str], *, user_id: int, organization_id: int, session_id: Optional[str],
                   statuses: Sequence[str] = (PENDING,), now: Optional[float] = None) -> Optional[PendingAction]:
    """The newest action of these kinds and states in this chat, for "change the draft" follow-ups. Pending
    ones that expired are skipped."""
    if not session_id:
        return None
    ts = now or time.time()
    best: Optional[PendingAction] = None
    for path in _dir().glob("act_*.json"):
        action = _load(path.stem)
        if (action is None or action.kind not in kinds or action.user_id != int(user_id)
                or action.organization_id != int(organization_id) or action.session_id != session_id
                or action.status not in statuses or action.expired(ts)):
            continue
        if best is None or action.created_at > best.created_at:
            best = action
    return best


def sweep(now: Optional[float] = None) -> List[str]:
    """Delete expired actions (and done or discarded ones past their expiry). Returns their ids."""
    ts = now or time.time()
    gone: List[str] = []
    for path in _dir().glob("act_*.json"):
        action = _load(path.stem)
        if action is None or ts < action.expires_at or action.status == CONFIRMING:
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            gone.append(action.id)
    return gone
