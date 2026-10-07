"""
shadow.py — Run the agent next to the current answer path, off the request path.

With AGENT_MODE=shadow the user sees the current path's answer. After it is
final, the agent answers the same question in a background thread and both
answers go to AGENT_SHADOW_LOG, one JSON line per question, for side-by-side
review. Never raises, never delays the response.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.agent.shadow")

MAX_BYTES = 10 * 1024 * 1024
MAX_ANSWER_CHARS = 6000
#: Agent runs allowed at once; a question arriving while all are busy is not shadowed.
MAX_CONCURRENT = 2
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

OrgMeta = Union[Dict[int, Dict[str, Any]], Callable[[], Dict[int, Dict[str, Any]]]]


def enabled() -> bool:
    return (settings.agent_mode or "").strip().lower() == "shadow"


def log_path() -> Optional[Path]:
    raw = (settings.agent_shadow_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def thread_history(session_id: Optional[str], db_name: str = "") -> List[Dict[str, Any]]:
    """The thread's earlier turns, read before the current turn is saved. [] when there is none."""
    if not enabled() or not session_id:
        return []
    try:
        from gemini_brain.memory.session_memory import get_history_by_session
        return get_history_by_session(session_id, limit=6, db_name=db_name)
    except Exception as e:  # noqa: BLE001
        logger.debug("agent shadow: no history for %s: %s", session_id, e)
        return []


def start(question: str, organization_ids: Sequence[int], org_meta: OrgMeta,
          final: Any, *, history: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[threading.Thread]:
    """Answer the question with the agent in the background and log both answers. None when off or busy.

    `org_meta` may be a function: it is then called in the background thread, so looking up
    organization names never delays the response.
    """
    if not enabled() or not organization_ids or not question:
        return None
    if not _slots.acquire(blocking=False):
        logger.info("agent shadow: %d runs already in progress; question not shadowed", MAX_CONCURRENT)
        return None
    current = final if isinstance(final, dict) else {}
    thread = threading.Thread(
        target=_run, args=(question, list(organization_ids), org_meta, current, list(history or [])),
        name="agent-shadow", daemon=True,
    )
    thread.start()
    return thread


def _run(question: str, orgs: List[int], org_meta: OrgMeta,
         current: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
    try:
        from gemini_brain.agent.loop import run_agent
        from gemini_brain.pii.redactor import redact_pii

        meta = org_meta() if callable(org_meta) else dict(org_meta or {})
        result = run_agent(question, orgs, meta, history=history, subject="agent-shadow")
        routing = current.get("routing_info") or {}
        _write({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "org_ids": orgs,
            "question": redact_pii(question)[0],
            "followup": bool(history),
            "current": {
                "answer": (current.get("answer") or "")[:MAX_ANSWER_CHARS],
                "status": current.get("status"),
                "path": routing.get("path"),
                "layout": routing.get("layout"),
            },
            "agent": {
                "answer": result.answer[:MAX_ANSWER_CHARS],
                "status": result.status,
                "elapsed_ms": result.elapsed_ms,
                "tool_calls": result.tool_calls,
                "verification": result.verification,
                "usage": result.usage,
            },
        })
    except Exception as e:  # noqa: BLE001 - shadow must never surface
        logger.warning("agent shadow failed: %s", e)
    finally:
        _slots.release()


def _write(record: Dict[str, Any]) -> None:
    path = log_path()
    if path is None:
        return
    try:
        line = json.dumps(record, default=str, ensure_ascii=False) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                backup = path.with_suffix(path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                os.replace(path, backup)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent shadow: could not write record: %s", e)
