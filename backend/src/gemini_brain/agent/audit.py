"""audit.py: a log of what the assistant flagged and what its guards did, one JSON line each.

Kinds of event:
  vat_review            a VAT review ran: who asked, for which organizations and period, how many findings of each kind
                        and severity, and the document numbers of the findings shown on the card
  injection_in_file     an uploaded file holds text that tries to give orders to the assistant
  injection_in_results  a tool result (the books) holds such text
  unchecked_books_claim an answer said whether a bill is in the books without a look at the books (the guard stepped in)
  canary_hit            an answer carried the secret marker of the system prompt, so it was replaced

A line holds ids, counts, pattern ids and document numbers. Never file text, never an amount, never an answer. The file
is AGENT_AUDIT_LOG (default logs/agent_audit.jsonl, relative to backend/); an empty value turns it off. Writing never
raises and never changes an answer.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from gemini_brain.agent import answer_log
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.agent.audit")
_lock = threading.Lock()
_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def log_path() -> Optional[Path]:
    raw = (settings.agent_audit_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def event(kind: str, **fields: Any) -> None:
    """Write one event. Never raises."""
    try:
        path = log_path()
        if path is None:
            return
        entry = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "kind": kind, **fields}
        line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > answer_log.MAX_BYTES:
                answer_log._rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as e:  # noqa: BLE001 - an audit line must never affect the answer
        logger.warning("audit: could not write %s: %s", kind, e)


def review_event(item: dict, *, user_id: Any, session_id: Optional[str], organization_ids: Any) -> None:
    """The audit line for one review_vat result of a turn (the tool's own input and result)."""
    result = item.get("result") or {}
    params = item.get("input") or {}
    shown = [str(b.get("document_number") or "") for g in result.get("groups") or [] for b in g.get("bills") or []]
    shown += [str((f.get("bill") or {}).get("document_number") or "") for f in result.get("findings") or []]
    event("vat_review", user_id=user_id, session_id=session_id, organization_ids=[int(o) for o in organization_ids or []],
          mode=result.get("mode"), period=result.get("period"), rules_asked=params.get("rules") or None,
          bills_checked=result.get("bills_checked"), duplicate_groups=result.get("groups_found"),
          duplicates_by_rule=result.get("groups_by_rule"), findings=result.get("findings_found"),
          findings_by_rule=result.get("findings_by_rule"), findings_by_severity=result.get("findings_by_severity"),
          patterns=[p.get("rule") for p in result.get("patterns") or []],
          scans_complete=all(o.get("complete") for o in result.get("organizations") or []),
          shown_documents=[n for n in shown if n][:50])
