"""
window_widener.py — Retry an empty date-scoped query over a longer look-back.

A query that returns zero rows used to end the conversation with a canned line
asking the *user* to widen their date range. The reference implementation instead
tells its planner to do that itself ("0 rows this month -> show last 30/60/90
days") and has the turns left to act on it. This is that behaviour, made explicit
and bounded.

**When widening is legitimate, and when it is not.** Widening answers a slightly
different question than the one asked, so it is only safe when the user's window
was open-ended at the recent end — "this month", "this quarter", "the last 30
days", or the default. If they asked about Q1 2025, or 2024, or last month, that
is a deliberate historical choice: silently reporting a different period's numbers
would be worse than saying there is nothing there.

The test is the data, not the phrase: a window whose end date is today (or
yesterday) is a "recent" window and may be widened. Any window that ends in the
past is left alone. That needs no plumbing of the original period phrase and
holds for every params class in tools/schemas.py, all of which resolve to
start_date/end_date via router/dates.py.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.router.dates import today as org_today

logger = logging.getLogger("gemini_brain.endpoints.window_widener")

#: Look-back windows to try, in days. Only rungs strictly longer than what the
#: user already asked for are considered — re-running the same span would just
#: spend an API call to get the same empty answer.
WIDENING_RUNGS_DAYS: Tuple[int, ...] = (90, 180, 365, 730)

#: Extra retrievals allowed per query. Each one is an API round trip (~225ms p50),
#: and it only happens on an otherwise-dead-end answer — but it is still latency
#: spent on a query that already failed once, so keep it small.
MAX_WIDENING_ATTEMPTS: int = 2

#: How far behind today a window may end and still count as "recent". One day of
#: slack absorbs timezone edges between the org clock and the backend's.
RECENT_END_TOLERANCE_DAYS: int = 1


def _parse_iso(value: Any) -> Optional[datetime.date]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except (ValueError, AttributeError):
        return None


def describe_window(start: str, end: str) -> str:
    """Human phrasing for a widened window, for the answer text."""
    s, e = _parse_iso(start), _parse_iso(end)
    if not s or not e:
        return "a longer period"
    days = (e - s).days
    if days >= 700:
        return "the last two years"
    if days >= 350:
        return "the last 12 months"
    if days >= 170:
        return "the last 6 months"
    if days >= 85:
        return "the last 3 months"
    return f"the last {days} days"


def plan_widenings(
    query_params: Optional[Dict[str, Any]],
    *,
    anchor: Optional[datetime.date] = None,
    max_attempts: int = MAX_WIDENING_ATTEMPTS,
) -> List[Tuple[str, str]]:
    """Widened [start_date, end_date] windows to retry, shortest first.

    Returns an empty list when widening does not apply: no date range in the
    params, unparseable dates, or a window that ends in the past (a deliberate
    historical period, which must not be silently replaced).
    """
    if not isinstance(query_params, dict):
        return []

    start = _parse_iso(query_params.get("start_date"))
    end = _parse_iso(query_params.get("end_date"))
    if start is None or end is None:
        return []

    ref = anchor if anchor is not None else org_today()

    # A window ending in the past is a deliberate historical choice — leave it.
    if (ref - end).days > RECENT_END_TOLERANCE_DAYS:
        logger.debug(
            "Not widening: window ends %s, which is historical relative to %s", end, ref
        )
        return []

    current_span = max((end - start).days, 0)
    plans: List[Tuple[str, str]] = []
    for rung in WIDENING_RUNGS_DAYS:
        if rung <= current_span:
            continue
        plans.append(((ref - datetime.timedelta(days=rung)).isoformat(), ref.isoformat()))
        if len(plans) >= max_attempts:
            break
    return plans


def widened_params(query_params: Dict[str, Any], start: str, end: str) -> Dict[str, Any]:
    """Copy of query_params with the date range replaced. Never mutates the input."""
    widened = dict(query_params)
    widened["start_date"] = start
    widened["end_date"] = end
    return widened
