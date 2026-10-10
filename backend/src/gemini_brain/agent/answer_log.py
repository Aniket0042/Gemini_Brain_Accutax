"""
answer_log.py — One JSON line per agent answer, for reviewing real use.

Every answer the agent gives is written to
AGENT_ANSWER_LOG: the question with personal data masked, the route, each tool
call, the figure check, time, tokens, cost, status and the answer. Reviewing it
shows what users ask, which answers failed or could not be verified, and where
the time and money go (scripts/agent_log_report.py summarises it).

User and organization ids are kept, emails are not. The file rotates at
MAX_BYTES, keeping BACKUPS older files. Writing never raises and never changes
the answer.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.agent.answer_log")

MAX_BYTES = 50 * 1024 * 1024
BACKUPS = 5
MAX_ANSWER_CHARS = 6000
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()


def log_path() -> Optional[Path]:
    raw = (settings.agent_answer_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def record(question: str, organization_ids: Sequence[int], *, user_id: Any, session_id: Optional[str],
           brief: bool, attachment: Optional[str], result: Any, response: Dict[str, Any]) -> None:
    """Log one answer. `result` is the AgentResult, `response` the envelope sent to the user."""
    try:
        from gemini_brain.pii.redactor import redact_pii
        usage = response.get("token_usage") or {}
        verification = response.get("verification") or {}
        notice = response.get("notice") or {}
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "user_id": user_id,
            "session_id": session_id,
            "organization_ids": [int(o) for o in organization_ids],
            "question": redact_pii(question or "")[0],
            "brief": bool(brief),
            "attachment": attachment,
            "route": getattr(result, "route", None),
            "agent_status": getattr(result, "status", None),
            "status": response.get("status"),
            "notice": notice.get("code"),
            "elapsed_s": usage.get("elapsed_seconds"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": usage.get("cost_usd"),
            "llm_calls": usage.get("llm_calls"),
            "tool_calls": [{k: c.get(k) for k in ("name", "input", "ok", "ms", "rows", "error")}
                           for c in getattr(result, "tool_calls", None) or []],
            "verification": {k: verification.get(k) for k in ("grounded", "checked", "matched", "unmatched")}
                            if verification else None,
            "blocks": _block_types(response.get("blocks")),
            "answer": (response.get("answer") or "")[:MAX_ANSWER_CHARS],
        }
        _write(entry)
    except Exception as e:  # noqa: BLE001 - logging must never affect the answer
        logger.warning("agent answer log: record not built: %s", e)


def _block_types(blocks: Any) -> List[str]:
    return [str(b.get("type")) for b in blocks or [] if isinstance(b, dict)]


def _write(entry: Dict[str, Any]) -> None:
    path = log_path()
    if path is None:
        return
    try:
        line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent answer log: could not write: %s", e)


def _rotate(path: Path) -> None:
    """agent_answers.jsonl -> .1 -> .2 ... ; the oldest beyond BACKUPS is dropped."""
    oldest = path.with_suffix(path.suffix + f".{BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for n in range(BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{n}")
        if src.exists():
            os.replace(src, path.with_suffix(path.suffix + f".{n + 1}"))
    os.replace(path, path.with_suffix(path.suffix + ".1"))
