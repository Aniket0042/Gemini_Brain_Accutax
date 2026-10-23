"""
ledger.py — One JSON line per piece of AI spend: who asked, for which organizations, which owner pays, and the cost.

Step 1 of docs/ORG_TOKEN_LIMITS_STRATEGY.md: record only, enforce nothing. The lines size the plans and caps later.

    with ledger.scope("agent_answer", user_id=7, organization_ids=[24, 25], session_id=sid) as spend:
        ...                   # every model call inside, in this thread or a copied context, is added
        spend.status = "ok"

BedrockAdapter calls add_call() for each model call. A call outside any scope (a report built outside a chat, a
health check) is written alone with kind "unscoped", so no spend is missed.

1 credit = $0.001 of model cost. An organization's owner is organizations.user_id. A scope that reads the
organizations of several owners is charged to them in equal parts. Writing never raises and never changes an
answer. USAGE_LEDGER empty (the default) turns it off.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.usage.ledger")

CREDITS_PER_USD = 1000
#: What the limit check will hold before a question (the 90th percentile question, in credits). Recorded with
#: each line so the hold can be compared with the real cost before enforcement starts.
RESERVE_CREDITS = 35
MAX_BYTES = 50 * 1024 * 1024
BACKUPS = 5
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_write_lock = threading.Lock()
_OWNER_CACHE: Dict[int, Optional[int]] = {}


@dataclass
class Scope:
    kind: str
    user_id: Optional[int]
    organization_ids: List[int]
    session_id: Optional[str] = None
    status: str = "ok"
    calls: List[Dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_current: ContextVar[Optional[Scope]] = ContextVar("usage_scope", default=None)


def log_path() -> Optional[Path]:
    raw = (settings.usage_ledger or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


@contextmanager
def scope(kind: str, *, user_id: Any, organization_ids: Sequence[Any], session_id: Optional[str] = None) -> Iterator[Scope]:
    """Collect the model calls made inside, and write them as one line on the way out (also after an error)."""
    try:
        orgs = [int(o) for o in organization_ids or [] if o is not None]
    except (TypeError, ValueError):
        orgs = []
    s = Scope(kind=kind, user_id=_int(user_id), organization_ids=orgs, session_id=session_id)
    token = _current.set(s)
    try:
        yield s
    except BaseException:
        s.status = "error"
        raise
    finally:
        _current.reset(token)
        if log_path() is not None:
            _write(_entry(s))


def current() -> Optional[Scope]:
    return _current.get()


@contextmanager
def attach(s: Optional[Scope]) -> Iterator[None]:
    """Count the calls of a worker thread in `s` (a scope taken from the thread that started the work)."""
    token = _current.set(s)
    try:
        yield
    finally:
        _current.reset(token)


def add_call(model_id: str, label: str, purpose: str, usage: Dict[str, Any]) -> None:
    """One model call, with Bedrock's usage dict (inputTokens, outputTokens, cacheReadInputTokens, ...)."""
    try:
        if log_path() is None:
            return
        call = _call(model_id, label, purpose, usage or {})
        if not any(call[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")):
            return  # the call failed before the model ran: nothing was billed
        s = _current.get()
        if s is None:
            alone = Scope(kind="unscoped", user_id=None, organization_ids=[], status="ok", calls=[call])
            _write(_entry(alone))
            return
        with s.lock:
            s.calls.append(call)
    except Exception as e:  # noqa: BLE001 - metering must never affect a model call
        logger.warning("usage ledger: call not recorded: %s", e)


def call_cost_usd(model_id: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    """The same price rule as BedrockAdapter.get_token_usage: cache writes at 1.25 times input, reads at a tenth."""
    from gemini_brain.reasoning.bedrock_client import bedrock_price_for_model
    rates = bedrock_price_for_model(model_id)
    return ((input_tokens / 1_000_000) * rates["input"]
            + (cache_write / 1_000_000) * rates["input"] * 1.25
            + (cache_read / 1_000_000) * rates["input"] * 0.10
            + (output_tokens / 1_000_000) * rates["output"])


def _call(model_id: str, label: str, purpose: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    tokens = {
        "input_tokens": int(usage.get("inputTokens") or 0),
        "output_tokens": int(usage.get("outputTokens") or 0),
        "cache_read_tokens": int(usage.get("cacheReadInputTokens") or 0),
        "cache_write_tokens": int(usage.get("cacheWriteInputTokens") or 0),
    }
    cost = call_cost_usd(model_id, tokens["input_tokens"], tokens["output_tokens"],
                         tokens["cache_read_tokens"], tokens["cache_write_tokens"])
    return {"model": model_id, "label": label, "purpose": purpose, **tokens, "cost_usd": round(cost, 6)}


def _entry(s: Scope) -> Dict[str, Any]:
    with s.lock:
        calls = list(s.calls)
    models: Dict[str, Dict[str, Any]] = {}
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    cost = 0.0
    for c in calls:
        m = models.setdefault(c["model"], {"calls": 0, **{k: 0 for k in totals}, "cost_usd": 0.0})
        m["calls"] += 1
        for k in totals:
            m[k] += c[k]
            totals[k] += c[k]
        m["cost_usd"] = round(m["cost_usd"] + c["cost_usd"], 6)
        cost += c["cost_usd"]
    credits = round(cost * CREDITS_PER_USD, 3)
    owners = owners_of(s.organization_ids)
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "kind": s.kind,
        "status": s.status,
        "user_id": s.user_id,
        "session_id": s.session_id,
        "organization_ids": s.organization_ids,
        "owners": {str(o): owners.get(o) for o in s.organization_ids},
        "relationship": {str(o): _relationship(s.user_id, owners.get(o)) for o in s.organization_ids},
        "charge": split_charge(credits, [owners.get(o) for o in s.organization_ids]),
        "calls": len(calls),
        "models": models,
        **totals,
        "cost_usd": round(cost, 6),
        "credits": credits,
        "reserved_credits": RESERVE_CREDITS if s.kind == "agent_answer" else 0,
        "elapsed_s": round(time.monotonic() - s.started, 2),
    }


def split_charge(credits: float, owners: Sequence[Optional[int]]) -> Dict[str, float]:
    """Credits per paying owner: equal parts across the distinct owners. An organization whose owner is not
    known is charged to "unknown"; no organization at all to "none"."""
    payers = list(dict.fromkeys("unknown" if o is None else str(o) for o in owners)) or ["none"]
    share = round(credits / len(payers), 3)
    return {p: share for p in payers}


def _relationship(user_id: Optional[int], owner: Optional[int]) -> str:
    if owner is None or user_id is None:
        return "unknown"
    return "owner" if owner == user_id else "other"


def owners_of(organization_ids: Sequence[int]) -> Dict[int, Optional[int]]:
    """organization id -> owner user id (organizations.user_id), cached in the process. Missing when the
    database cannot answer; the line is still written."""
    todo = [o for o in organization_ids if o not in _OWNER_CACHE]
    if todo:
        try:
            from gemini_brain.db.connection import get_connection
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SELECT id, user_id FROM public.organizations WHERE id = ANY(%s);", (todo,))
                found = {int(r[0]): (int(r[1]) if r[1] is not None else None) for r in cur.fetchall()}
            finally:
                cur.close()
                conn.close()
            for o in todo:
                _OWNER_CACHE[o] = found.get(o)
        except Exception as e:  # noqa: BLE001
            logger.warning("usage ledger: owners not looked up: %s", e)
    return {o: _OWNER_CACHE.get(o) for o in organization_ids}


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _write(entry: Dict[str, Any]) -> None:
    path = log_path()
    if path is None:
        return
    try:
        line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as e:  # noqa: BLE001
        logger.warning("usage ledger: could not write: %s", e)


def _rotate(path: Path) -> None:
    """ai_usage.jsonl -> .1 -> .2 ... ; the oldest beyond BACKUPS is dropped."""
    oldest = path.with_suffix(path.suffix + f".{BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for n in range(BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{n}")
        if src.exists():
            os.replace(src, path.with_suffix(path.suffix + f".{n + 1}"))
    os.replace(path, path.with_suffix(path.suffix + ".1"))
