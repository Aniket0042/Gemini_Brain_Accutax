"""
snapshots.py — Before/after proof of PII masking, one record per request (PII plan, phase 4).

For a request the masking covers (PII_MODE shadow or on) and PII_SNAPSHOTS selects, every model call is kept as:

  raw    what the request held before masking: the system prompt and messages with real names and identifiers,
         the answer the user saw, and the placeholder -> value map. It holds personal data, so it is written
         encrypted (Fernet, PII_SNAPSHOT_KEY) and deleted after PII_SNAPSHOT_RAW_DAYS. Without a key it is not
         written at all.
  sent   what the model received (or, in shadow mode, would have received), the model's reply as written, and
         the sentinel's scan of what was sent. No personal data by construction, so it is plain JSON, kept
         PII_SNAPSHOT_DAYS.

Files: <PII_SNAPSHOT_DIR>/<YYYY-MM-DD>/<id>.json (sent) and <id>.raw (encrypted raw). The admin page reads them
through api/privacy_routes.py, for PII_SNAPSHOT_ADMINS only. Writing never raises and never changes an answer.
"""
from __future__ import annotations

import datetime
import json
import logging
import random
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.privacy.snapshots")

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAX_STRING = 20000
MAX_ITEMS = 300
_ID = re.compile(r"^[0-9a-f]{16}$")
_lock = threading.Lock()
_last_sweep = 0.0


# ── Settings ────────────────────────────────────────────────────────────────

def _people(raw: str) -> set:
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}


def enabled() -> bool:
    return (settings.pii_snapshots or "off").strip().lower() == "on"


def wanted(user_id: Any = None) -> bool:
    """Whether to keep a snapshot of this request: PII_SNAPSHOTS=on, the user in PII_SNAPSHOT_USERS (when set),
    and the request in the PII_SNAPSHOT_SAMPLE_PCT sample."""
    if not enabled():
        return False
    users = _people(settings.pii_snapshot_users)
    if users and str(user_id).lower() not in users:
        return False
    pct = max(0, min(100, int(settings.pii_snapshot_sample_pct or 0)))
    return pct >= 100 or random.random() * 100 < pct


def is_admin(user_id: Any, email: Optional[str]) -> bool:
    """Whether a user may read snapshots (PII_SNAPSHOT_ADMINS: emails or user ids). Empty: nobody."""
    admins = _people(settings.pii_snapshot_admins)
    return bool(admins) and (str(user_id).lower() in admins or (email or "").strip().lower() in admins)


def directory() -> Path:
    raw = (settings.pii_snapshot_dir or "logs/pii_snapshots").strip()
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def _fernet():
    key = (settings.pii_snapshot_key or "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


# ── Capture ─────────────────────────────────────────────────────────────────

def _jsonable(value: Any, depth: int = 0) -> Any:
    """A JSON-safe, size-capped copy: file bytes become a note, long strings and lists are cut with a marker."""
    if depth > 24:
        return "…"
    if isinstance(value, (bytes, bytearray)):
        return f"<file, {len(value)} bytes>"
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + f"… [{len(value) - MAX_STRING} more characters]"
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_jsonable(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"… [{len(value) - MAX_ITEMS} more items]")
        return items
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _system_text(system: Any) -> str:
    if isinstance(system, str):
        return system
    return "\n".join(b["text"] for b in system or [] if isinstance(b, dict) and isinstance(b.get("text"), str))


class Capture:
    """The calls of one request, kept until `save`."""

    def __init__(self, *, user_id: Any = None, session_id: Optional[str] = None,
                 organization_ids: Sequence[int] = (), mode: str = "on"):
        self.id = secrets.token_hex(8)
        self.ts = datetime.datetime.now(datetime.timezone.utc)
        self.user_id = user_id
        self.session_id = session_id
        self.organization_ids = [int(o) for o in organization_ids or ()]
        self.mode = mode
        self.calls: List[Dict[str, Any]] = []

    def add(self, *, purpose: str, model: str = "", raw_system: Any = "", raw_messages: Sequence[Any] = (),
            sent_system: Any = "", sent_messages: Sequence[Any] = (), reply_model: str = "",
            reply_user: str = "", question_index: Optional[int] = None) -> None:
        """One model call: what the request held, what the model got, what it wrote, what the user saw."""
        try:
            from gemini_brain.privacy import sentinel
            found = sentinel.scan(sent_system, list(sent_messages))["found"]
        except Exception as e:  # noqa: BLE001
            logger.warning("pii snapshot: sentinel scan failed: %s", e)
            found = None
        self.calls.append({
            "purpose": purpose, "model": model,
            "raw": {"system": _system_text(raw_system), "messages": _jsonable(list(raw_messages))},
            "sent": {"system": _system_text(sent_system), "messages": _jsonable(list(sent_messages))},
            "reply_model": _jsonable(reply_model or ""), "reply_user": _jsonable(reply_user or ""),
            "question_index": question_index,
            "sentinel": found,
        })


def begin(masking: Any, *, user_id: Any = None, session_id: Optional[str] = None,
          organization_ids: Sequence[int] = (), force: bool = False) -> None:
    """Start a capture on this request's masking, when this request is to be kept (or `force`: the live demo)."""
    try:
        if masking is not None and getattr(masking, "capture", None) is None and (force or wanted(user_id)):
            masking.capture = Capture(user_id=user_id, session_id=session_id, organization_ids=organization_ids,
                                      mode=masking.mode)
            masking.capture.vault = masking.vault  # the live demo highlights real values with it
    except Exception as e:  # noqa: BLE001
        logger.warning("pii snapshot: not started: %s", e)


def save(masking: Any) -> Optional[str]:
    """Write (or rewrite, after a later call of the same request) the snapshot. Returns its id. Never raises."""
    capture = getattr(masking, "capture", None)
    if capture is None or not capture.calls:
        return None
    try:
        day = directory() / capture.ts.strftime("%Y-%m-%d")
        sent = {
            "id": capture.id, "ts": capture.ts.isoformat(timespec="seconds"), "user_id": capture.user_id,
            "session_id": capture.session_id, "organization_ids": capture.organization_ids, "mode": capture.mode,
            "stats": masking.stats() if hasattr(masking, "stats") else {},
            "calls": [{k: c[k] for k in ("purpose", "model", "sent", "reply_model", "sentinel", "question_index")} for c in capture.calls],
        }
        fernet = _fernet()
        sent["raw_kept"] = fernet is not None
        with _lock:
            day.mkdir(parents=True, exist_ok=True)
            (day / f"{capture.id}.json").write_text(json.dumps(sent, ensure_ascii=False, default=str), encoding="utf-8")
            if fernet is not None:
                raw = {"vault": dict(getattr(masking.vault, "_value_of", {})),
                       "calls": [{"raw": c["raw"], "reply_user": c["reply_user"]} for c in capture.calls]}
                (day / f"{capture.id}.raw").write_bytes(
                    fernet.encrypt(json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8")))
        _sweep_now_and_then()
        return capture.id
    except Exception as e:  # noqa: BLE001 - a snapshot must never break the answer
        logger.warning("pii snapshot: not written: %s", e)
        return None


# ── Reading (admin page) ────────────────────────────────────────────────────

def _files(snapshot_id: str) -> Optional[Path]:
    if not _ID.match(snapshot_id or ""):
        return None
    for day in sorted(directory().glob("????-??-??"), reverse=True):
        path = day / f"{snapshot_id}.json"
        if path.exists():
            return path
    return None


def is_test_run(user_id: Any) -> bool:
    """Requests with no signed-in user: evals and smoke tests run from the server (user 0)."""
    return user_id in (None, "", 0, "0")


def recent(limit: int = 50, user_id: Any = None, include_test: bool = True) -> List[Dict[str, Any]]:
    """Summaries of the newest snapshots, newest first. `include_test=False` leaves out test runs."""
    out: List[Dict[str, Any]] = []
    root = directory()
    if not root.exists():
        return out
    for day in sorted(root.glob("????-??-??"), reverse=True):
        for path in sorted(day.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                snap = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if user_id is not None and str(snap.get("user_id")) != str(user_id):
                continue
            if not include_test and is_test_run(snap.get("user_id")):
                continue
            leaks = [c.get("sentinel") for c in snap.get("calls") or []]
            out.append({
                "id": snap.get("id"), "ts": snap.get("ts"), "user_id": snap.get("user_id"),
                "organization_ids": snap.get("organization_ids"), "mode": snap.get("mode"),
                "purposes": [c.get("purpose") for c in snap.get("calls") or []],
                "hidden": sum((snap.get("stats") or {}).get("hidden", {}).values()),
                "sentinel_pass": all(isinstance(f, dict) and not f for f in leaks) if leaks else None,
                "raw_kept": bool(snap.get("raw_kept")) and path.with_suffix(".raw").exists(),
            })
            if len(out) >= limit:
                return out
    return out


def load(snapshot_id: str, *, include_raw: bool = True) -> Optional[Dict[str, Any]]:
    """One snapshot: the sent side, and the decrypted raw side when kept and asked for."""
    path = _files(snapshot_id)
    if path is None:
        return None
    snap = json.loads(path.read_text(encoding="utf-8"))
    snap["raw_available"] = False
    raw_path = path.with_suffix(".raw")
    if include_raw and raw_path.exists():
        fernet = _fernet()
        if fernet is not None:
            try:
                raw = json.loads(fernet.decrypt(raw_path.read_bytes()).decode("utf-8"))
                for call, extra in zip(snap.get("calls") or [], raw.get("calls") or []):
                    call.update(extra)
                snap["vault"] = raw.get("vault") or {}
                snap["raw_available"] = True
            except Exception as e:  # noqa: BLE001 - a changed key: the sent side still shows
                logger.warning("pii snapshot %s: raw side not readable: %s", snapshot_id, e)
    return snap


# ── Retention ───────────────────────────────────────────────────────────────

def sweep(now: Optional[datetime.date] = None) -> int:
    """Delete raw sides older than PII_SNAPSHOT_RAW_DAYS and whole days older than PII_SNAPSHOT_DAYS."""
    today = now or datetime.datetime.now(datetime.timezone.utc).date()
    removed = 0
    root = directory()
    if not root.exists():
        return 0
    for day in root.glob("????-??-??"):
        try:
            age = (today - datetime.date.fromisoformat(day.name)).days
        except ValueError:
            continue
        if age > int(settings.pii_snapshot_days):
            shutil.rmtree(day, ignore_errors=True)
            removed += 1
        elif age > int(settings.pii_snapshot_raw_days):
            for raw in day.glob("*.raw"):
                raw.unlink(missing_ok=True)
                removed += 1
    return removed


def _sweep_now_and_then() -> None:
    global _last_sweep
    if time.monotonic() - _last_sweep > 3600:
        _last_sweep = time.monotonic()
        try:
            sweep()
        except Exception as e:  # noqa: BLE001
            logger.warning("pii snapshot: sweep failed: %s", e)
