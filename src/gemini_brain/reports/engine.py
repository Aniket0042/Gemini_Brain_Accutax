"""
engine.py — Execution layer for deterministic SQL reports.

These reports answer questions the REST catalog does not cover — segmented P&L,
invoice-level aging, contact statements, VAT detail. Without them those questions
miss the router entirely and fall to the NL-to-SQL tier, which costs 5.9s p50 and
22.3s p95 and asks a model to invent SQL that is already written and proven here.

**Why inline SQL rather than stored functions.** The three existing SQL-backed
tools (fn_project_expense_rollup and friends) are Postgres functions applied from
sql/functions/*.sql. That pattern is fine, but it makes the tool inert until
somebody runs the migration — and a RETURNS TABLE signature has to match the
query's column types exactly or it fails at call time. These reports ship working
with no deployment step. They can be promoted to stored functions later if the
plan caching turns out to matter.

Safety, in the order it is applied:
  1. assert_read_only on the SQL text — belt and braces over hand-vetted queries.
  2. A read-only transaction, so the database refuses a write even if 1 misses.
  3. SET LOCAL app.current_org for RLS, matching execute_sql_function.
  4. SET LOCAL statement_timeout, so a bad plan cannot hold a connection.
  5. org_id passed as a bound parameter, never interpolated.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gemini_brain.sql_fallback.db_connection import get_connection
from gemini_brain.sql_fallback.sql_safety import assert_read_only

logger = logging.getLogger("gemini_brain.reports.engine")

#: Per-statement ceiling. Lower than the fallback tier's 20s: these are indexed,
#: bounded reports, so anything slower than this is a bug rather than a big query.
REPORT_STATEMENT_TIMEOUT = "10s"

#: Hard row ceiling applied by each report's own LIMIT. Kept here as the number
#: the formatters and tests agree on.
REPORT_ROW_LIMIT = 50


def serialize(value: Any) -> Any:
    """JSON-safe scalar. Decimal -> float, date/datetime -> ISO string."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def serialize_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{k: serialize(v) for k, v in row.items()} for row in rows]


def query(
    sql: str,
    params: Tuple[Any, ...],
    org_id: int,
    db_name: str = "",
) -> List[Dict[str, Any]]:
    """Run one vetted read-only report query. Raises on failure — see run_report_safe."""
    assert_read_only(sql)

    conn = get_connection(db_name=db_name)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY;")
            cur.execute("SET LOCAL app.current_org = %s;", (str(org_id),))
            cur.execute("SET LOCAL statement_timeout = %s;", (REPORT_STATEMENT_TIMEOUT,))
            cur.execute(sql, params)
            if cur.description is None:
                conn.rollback()
                return []
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        # Read-only transaction: nothing to commit, and rollback releases cleanly.
        conn.rollback()
        return serialize_rows(rows)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def parse_date(value: Optional[str], fallback: datetime.date) -> str:
    """Normalise an ISO date string, falling back when absent or unparseable."""
    if not value:
        return fallback.isoformat()
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        return fallback.isoformat()


def total_of(rows: Sequence[Dict[str, Any]], key: str) -> float:
    """Sum one numeric column across rows, tolerating nulls and non-numerics."""
    total = 0.0
    for row in rows:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return round(total, 2)


def run_report_safe(
    report_key: str,
    query_params: Dict[str, Any],
    org_id: int,
    db_name: str = "",
) -> Any:
    """Execute a registered report and return a Retrieved outcome. Never raises.

    `report_key` is the tool's endpoint (e.g. "rpt_aged_receivables_detail").
    """
    from gemini_brain.reports.definitions import REPORTS
    from gemini_brain.resilience.errors import ErrorCode, classify_exception
    from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload

    report: Optional[Callable[..., Dict[str, Any]]] = REPORTS.get(report_key)
    if report is None:
        logger.error("Unknown report requested: %s", report_key)
        return Retrieved(
            Outcome.INVALID,
            tier="sql_report",
            endpoint=report_key,
            reason="unknown_report",
        )

    try:
        payload = report(dict(query_params or {}), org_id, db_name)
    except Exception as e:
        code = classify_exception(e)
        outcome = Outcome.UNAVAILABLE if code == ErrorCode.DB_UNAVAILABLE else Outcome.INVALID
        logger.warning("Report %s failed (%s): %s", report_key, code.value, e)
        return Retrieved(
            outcome,
            tier="sql_report",
            endpoint=report_key,
            reason=code.value.lower(),
            detail=str(e)[:300],
        )

    return classify_payload(payload, tier="sql_report", endpoint=report_key)
