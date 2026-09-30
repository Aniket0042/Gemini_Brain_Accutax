"""
multi_org_log.py — A record of how each multi-organization question was answered.

Every multi-org answer appends one JSON line: the (PII-redacted) question, how
it was planned, which layout it got, and what it cost. Questions that fell
through to model routing or to the plain collapsed layout are marked
`matched: false`; those are the ones the metric, series and list rules do not
understand yet, and scripts/eval/multi_org_plan_report.py lists them.

Only the redacted question and counts are stored: no user or organization IDs.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.multi_org.plan_log")

#: Rotate the log file past this size (one backup kept).
MAX_BYTES = 5 * 1024 * 1024
#: Layouts the rules produced on their own; anything else is a gap to look at.
MATCHED_LAYOUTS = {"metric", "multi_metric", "series", "series_set", "per_org", "merged", "overlap", "direct",
                   "unsupported", "greeting"}

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()


def log_path() -> Optional[Path]:
    """Where records go; None when logging is turned off (empty setting)."""
    raw = (settings.multi_org_plan_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def plan_record(question: str, plan: Any, final: Dict[str, Any], org_count: int) -> Dict[str, Any]:
    """One log record for an answered multi-org question."""
    routing = final.get("routing_info") or {}
    layout = "direct" if routing.get("path") == "multi_org_direct" else routing.get("layout") or "unknown"
    source = getattr(plan, "source", None) or "none"
    usage = final.get("token_usage") or {}
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "orgs": org_count,
        "source": source,
        "kind": getattr(plan, "kind", None),
        "metrics": list(getattr(plan, "metrics", None) or ([plan.metric] if getattr(plan, "metric", None) else [])),
        "series": getattr(plan, "series", None),
        "endpoint": ((getattr(plan, "selection", None) or {}).get("endpoint")),
        "layout": layout,
        "status": final.get("status"),
        "llm_calls": usage.get("llm_calls"),
        "elapsed_seconds": usage.get("elapsed_seconds"),
        # Rules understood the question when a rule planned it and it got a
        # purpose-built layout; model routing or the plain collapsed view is a gap.
        "matched": source not in ("llm", "none") and layout in MATCHED_LAYOUTS,
    }


def record_plan(question: str, plan: Any, final: Dict[str, Any], org_count: int) -> None:
    """Append one record. Never raises: logging must not fail an answer."""
    try:
        from gemini_brain.pii.redactor import redact_pii

        safe_question, _ = redact_pii(question or "")
        record = plan_record(safe_question, plan, final, org_count)
        logger.info("multi_org plan %s", json.dumps(record, default=str))
        path = log_path()
        if path is None:
            return
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
    except Exception as e:  # noqa: BLE001 - diagnostics only
        logger.warning("multi_org: could not record plan: %s", e)


def read_records(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """All records in the current log file and its backup, oldest first."""
    path = path or log_path()
    if path is None:
        return []
    records: List[Dict[str, Any]] = []
    for candidate in (path.with_suffix(path.suffix + ".1"), path):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records
