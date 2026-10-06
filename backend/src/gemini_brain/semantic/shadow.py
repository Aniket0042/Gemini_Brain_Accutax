"""
shadow.py — Compare multi-org metric figures from SQL with Cube's, off the request path.

Phase 1 of docs/CUBE_CORE_INTEGRATION_GUIDE_V2.md. Users always see the SQL
figures. This only records, one JSON line per organization and metric, where
Cube differs, so every difference is explained before Cube answers for real.
It runs in a background thread after the answer is ready and never raises.

Revenue, expenses, net profit and margin move from documents (SQL reports) to
the posted ledger (Cube), so those records carry basis_change=true: their
differences are expected and are reviewed, not fixed.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.semantic.shadow")

#: Longest the background comparison may spend in Cube.
SHADOW_DEADLINE_SECONDS = 30.0
#: Rotate the log file past this size (one backup kept).
MAX_BYTES = 5 * 1024 * 1024
#: Differences at or below this are rounding.
TOLERANCE = Decimal("0.01")
#: Metrics whose source changes from documents (rpt_ reports) to the posted ledger.
BASIS_CHANGE = frozenset({"revenue", "expenses", "net_profit", "profit_margin"})

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()


def log_path() -> Optional[Path]:
    raw = (settings.metrics_shadow_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def start_shadow_compare(plan: Any, runs: Sequence[Any], question: str, *, user_id: int) -> Optional[threading.Thread]:
    """Compare this answer's metric figures with Cube in the background. None when nothing to compare."""
    try:
        jobs = _jobs(plan, runs)
    except Exception as e:  # noqa: BLE001 - shadow must never fail an answer
        logger.warning("metrics shadow: could not prepare comparison: %s", e)
        return None
    if not jobs:
        return None
    org_ids = [int(r.org_id) for r in runs]
    thread = threading.Thread(
        target=_compare, args=(jobs, org_ids, question, user_id),
        name="metrics-shadow", daemon=True,
    )
    thread.start()
    return thread


#: (metric key, (start, end, as_of), {org_id: (sql value, reason it is missing)})
Job = Tuple[str, Tuple[str, str, str], Dict[int, Tuple[Optional[float], str]]]


def _jobs(plan: Any, runs: Sequence[Any]) -> List[Job]:
    """What to compare, with the SQL figures read now, while the runs are final."""
    from gemini_brain.orchestrator.multi_org_metrics import BY_KEY, metric_value
    from gemini_brain.semantic.metric_table import MEMBER_FOR_METRIC

    if plan is None or getattr(plan, "series", None):
        return []
    keys = list(getattr(plan, "metrics", None) or ([plan.metric] if getattr(plan, "metric", None) else []))
    jobs: List[Job] = []
    today = datetime.date.today().isoformat()
    for key in keys:
        if key not in MEMBER_FOR_METRIC or key not in BY_KEY:
            continue
        metric = BY_KEY[key]
        selection = next((s for s in plan.all_selections() if s.get("key") == metric.fetch_key),
                         getattr(plan, "selection", None) or {})
        params = selection.get("query_params") or {}
        if metric.period_based:
            start = str(params.get("start_date") or f"{today[:4]}-01-01")
            end = str(params.get("end_date") or today)
            window = (start, end, end)
        else:
            as_of = str(params.get("as_of_date") or today)
            window = ("1900-01-01", as_of, as_of)
        jobs.append((key, window, {int(r.org_id): metric_value(metric, r) for r in runs}))
    return jobs


def _compare(jobs: List[Job], org_ids: List[int], question: str, user_id: int) -> None:
    from gemini_brain.pii.redactor import redact_pii
    from gemini_brain.semantic.metric_table import MEMBER_FOR_METRIC, metric_table

    try:
        safe_question, _ = redact_pii(question or "")
    except Exception:  # noqa: BLE001
        safe_question = ""
    deadline = time.monotonic() + SHADOW_DEADLINE_SECONDS
    by_window: Dict[Tuple[str, str, str], List[Job]] = {}
    for job in jobs:
        by_window.setdefault(job[1], []).append(job)

    for (start, end, as_of), window_jobs in by_window.items():
        keys = [k for k, _, _ in window_jobs]
        base = {"ts": _now(), "question": safe_question, "period": {"start": start, "end": end, "as_of": as_of}}
        t0 = time.monotonic()
        try:
            table, mixed = metric_table(keys, organization_ids=org_ids, start=start, end=end, as_of=as_of,
                                        subject=f"user:{user_id}:shadow", deadline=deadline)
        except Exception as e:  # noqa: BLE001 - recorded, never raised
            _write({**base, "metrics": keys, "error": f"{type(e).__name__}: {str(e)[:300]}"})
            continue
        cube_ms = int((time.monotonic() - t0) * 1000)
        for key, _, sql_values in window_jobs:
            for org in org_ids:
                sql_value, sql_reason = sql_values.get(org, (None, "missing"))
                cube_value = table.get(org, {}).get(key)
                _write({**base, **compare_record(key, MEMBER_FOR_METRIC[key], org, sql_value, sql_reason,
                                                 cube_value, org in mixed, key in table.get(org, {})),
                        "cube_ms": cube_ms})


def compare_record(
    key: str,
    member: str,
    org: int,
    sql_value: Optional[float],
    sql_reason: str,
    cube_value: Optional[Decimal],
    mixed_currency: bool,
    cube_has_row: bool,
) -> Dict[str, Any]:
    """One comparison line. `match` is None when either side has no figure."""
    difference = None
    match = None
    if sql_value is not None and cube_value is not None:
        difference = Decimal(str(cube_value)) - Decimal(str(sql_value))
        match = abs(difference) <= TOLERANCE
    return {
        "metric": key,
        "member": member,
        "org_id": org,
        "sql_value": sql_value,
        "sql_reason": sql_reason or None,
        "cube_value": float(cube_value) if cube_value is not None else None,
        "cube_reason": None if cube_value is not None else (
            "mixed currencies" if mixed_currency else ("empty" if cube_has_row else "nothing recorded")),
        "difference": float(difference) if difference is not None else None,
        "match": match,
        "basis_change": key in BASIS_CHANGE,
    }


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


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
    except Exception as e:  # noqa: BLE001 - diagnostics only
        logger.warning("metrics shadow: could not write record: %s", e)
