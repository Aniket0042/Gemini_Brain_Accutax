"""
dates.py — Deterministic, timezone-aware date range resolution for accounting periods.

Uses Asia/Dubai timezone (UTC+4) as the authoritative reference for UAE organizations.
"""
from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from typing import Optional

ORG_TZ = ZoneInfo("Asia/Dubai")


@dataclass(frozen=True)
class Window:
    """Immutable date window representing a closed interval [date_from, date_to]."""
    date_from: datetime.date
    date_to: datetime.date


def today(tz: datetime.tzinfo = ORG_TZ) -> datetime.date:
    """Return the current date in the organization's timezone (Asia/Dubai by default)."""
    return datetime.datetime.now(tz).date()


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


_NAMED_DAY = re.compile(r"\b(today|yesterday|tonight)\b", re.IGNORECASE)


def date_filters_from_request(
    query_params: Optional[dict] = None,
    phrase: Optional[str] = None,
    anchor: Optional[datetime.date] = None,
) -> dict:
    """Return {date_from, date_to} matching the REST window, or {} if unknown.

    Used so a SQL empty-result cross-check cannot silently drop the date filter
    the API already applied (e.g. "posted today" → REST empty → SQL returning
    a year of journals). REST query params win; otherwise a named day in the
    user's phrasing is resolved. Unrecognized phrases are not defaulted to
    this year — that would invent a window the REST call never used.
    """
    qp = query_params or {}
    start = qp.get("start_date") or qp.get("startDate")
    end = qp.get("end_date") or qp.get("endDate")
    if isinstance(start, str) and isinstance(end, str):
        s, e = start.strip()[:10], end.strip()[:10]
        if len(s) == 10 and len(e) == 10:
            return {"date_from": s, "date_to": e}
    p = (phrase or "").strip().lower()
    if not p:
        return {}
    if p in ("today", "yesterday", "tonight"):
        w = resolve(p, anchor=anchor)
        return {"date_from": w.date_from.isoformat(), "date_to": w.date_to.isoformat()}
    m = _NAMED_DAY.search(p)
    if m:
        w = resolve(m.group(1).lower(), anchor=anchor)
        return {"date_from": w.date_from.isoformat(), "date_to": w.date_to.isoformat()}
    return {}


def resolve(phrase: Optional[str] = None, anchor: Optional[datetime.date] = None) -> Window:
    """Resolve a natural language date/period phrase into a concrete [date_from, date_to] Window.

    Supported phrases:
      - "today", "yesterday"
      - "this month", "current month"
      - "last month", "previous month"
      - "this quarter", "current quarter"
      - "last quarter", "previous quarter"
      - "this year", "current year", "ytd"
      - "last year", "previous year"
      - "mtd" (month to date)
      - "qtd" (quarter to date)
      - "last N months" (e.g. "last 6 months", "last 3 months")
      - "last N days" (e.g. "last 30 days")
      - bare 4-digit year (e.g. "2025", "2026")
      - quarter-year syntax (e.g. "Q1 2026", "q3 2025")

    Defaults to 'this year' for unrecognized or omitted phrases.
    """
    ref = anchor if anchor is not None else today()
    p = (phrase or "").strip().lower()

    if not p:
        # Default: this year up to today
        return Window(date_from=ref.replace(month=1, day=1), date_to=ref)

    if p in ("today", "tonight"):
        return Window(date_from=ref, date_to=ref)
    if p == "yesterday":
        y = ref - datetime.timedelta(days=1)
        return Window(date_from=y, date_to=y)

    # 1. This month / current month / MTD
    if p in ("this month", "current month"):
        return Window(
            date_from=ref.replace(day=1),
            date_to=ref.replace(day=_last_day_of_month(ref.year, ref.month)),
        )
    if p == "mtd":
        return Window(date_from=ref.replace(day=1), date_to=ref)

    # 2. Last month / previous month
    if p in ("last month", "previous month"):
        if ref.month == 1:
            prev_year = ref.year - 1
            prev_month = 12
        else:
            prev_year = ref.year
            prev_month = ref.month - 1
        return Window(
            date_from=datetime.date(prev_year, prev_month, 1),
            date_to=datetime.date(prev_year, prev_month, _last_day_of_month(prev_year, prev_month)),
        )

    # 3. This quarter / current quarter / QTD
    current_q = (ref.month - 1) // 3 + 1
    q_start_month = (current_q - 1) * 3 + 1
    q_end_month = current_q * 3

    if p in ("this quarter", "current quarter"):
        return Window(
            date_from=datetime.date(ref.year, q_start_month, 1),
            date_to=datetime.date(ref.year, q_end_month, _last_day_of_month(ref.year, q_end_month)),
        )
    if p == "qtd":
        return Window(date_from=datetime.date(ref.year, q_start_month, 1), date_to=ref)

    # 4. Last quarter / previous quarter
    if p in ("last quarter", "previous quarter"):
        if current_q == 1:
            lq_year = ref.year - 1
            lq_start_m = 10
            lq_end_m = 12
        else:
            lq_year = ref.year
            lq_start_m = (current_q - 2) * 3 + 1
            lq_end_m = (current_q - 1) * 3
        return Window(
            date_from=datetime.date(lq_year, lq_start_m, 1),
            date_to=datetime.date(lq_year, lq_end_m, _last_day_of_month(lq_year, lq_end_m)),
        )

    # 5. Quarter + Year (e.g. "Q1 2026", "q3 2025") or Bare Quarter ("Q2", "quarter 2")
    q_match = re.match(r"^(?:q|quarter\s*)([1-4])(?:\s*(20\d{2}))?$", p)
    if q_match:
        q_num = int(q_match.group(1))
        q_yr = int(q_match.group(2)) if q_match.group(2) else ref.year
        s_m = (q_num - 1) * 3 + 1
        e_m = q_num * 3
        return Window(
            date_from=datetime.date(q_yr, s_m, 1),
            date_to=datetime.date(q_yr, e_m, _last_day_of_month(q_yr, e_m)),
        )

    # 6. This year / current year / YTD
    if p in ("this year", "current year"):
        return Window(
            date_from=datetime.date(ref.year, 1, 1),
            date_to=datetime.date(ref.year, 12, 31),
        )
    if p == "ytd":
        return Window(date_from=datetime.date(ref.year, 1, 1), date_to=ref)

    # 7. Last year / previous year
    if p in ("last year", "previous year"):
        return Window(
            date_from=datetime.date(ref.year - 1, 1, 1),
            date_to=datetime.date(ref.year - 1, 12, 31),
        )

    # 8. Bare 4-digit year (e.g. "2025", "2026")
    yr_match = re.match(r"^(20\d{2})$", p)
    if yr_match:
        yr = int(yr_match.group(1))
        return Window(
            date_from=datetime.date(yr, 1, 1),
            date_to=datetime.date(yr, 12, 31),
        )

    # 9. Last N months (e.g. "last 6 months", "last 3 months")
    n_mo_match = re.match(r"^last\s+(\d+)\s+months?$", p)
    if n_mo_match:
        n_months = int(n_mo_match.group(1))
        # N months ago starting from 1st of that month
        total_months = ref.year * 12 + ref.month - 1
        start_total = total_months - n_months
        start_year = start_total // 12
        start_month = (start_total % 12) + 1
        return Window(
            date_from=datetime.date(start_year, start_month, 1),
            date_to=ref,
        )

    # 10. Last N days (e.g. "last 30 days", "last 7 days")
    n_day_match = re.match(r"^last\s+(\d+)\s+days?$", p)
    if n_day_match:
        n_days = int(n_day_match.group(1))
        return Window(
            date_from=ref - datetime.timedelta(days=n_days),
            date_to=ref,
        )

    # Default fallback: this year
    return Window(
        date_from=datetime.date(ref.year, 1, 1),
        date_to=datetime.date(ref.year, 12, 31),
    )


# ── Forecast horizons ────────────────────────────────────────────────────────

_FORECAST_MONTHS = re.compile(
    r"\bnext\s+(\d+)\s+months?\b|\b(\d+)[-\s]month\b|\bnext\s+(\d+)\s+quarters?\b",
    re.IGNORECASE,
)
_FORECAST_WORDS = [
    (re.compile(r"\bnext\s+year\b|\b12[-\s]month\b", re.IGNORECASE), 12),
    (re.compile(r"\bnext\s+quarter\b", re.IGNORECASE), 3),
    (re.compile(r"\bnext\s+month\b", re.IGNORECASE), 1),
    (re.compile(r"\bnext\s+half\b|\bnext\s+6\s+months\b", re.IGNORECASE), 6),
]

#: Used when the question names no horizon at all.
DEFAULT_FORECAST_MONTHS = 6


def forecast_months(query: str, default: int = DEFAULT_FORECAST_MONTHS) -> int:
    """Months of forecast horizon requested in ``query``.

    "next 3 months" once produced the same 6-month window as "next 12 months",
    because the horizon was hardcoded. Bounded to 1..36 so a stray number in the
    question cannot ask for a century of projection.
    """
    m = _FORECAST_MONTHS.search(query or "")
    if m:
        if m.group(1) or m.group(2):
            months = int(m.group(1) or m.group(2))
        else:
            months = int(m.group(3)) * 3
        return max(1, min(months, 36))

    for pattern, months in _FORECAST_WORDS:
        if pattern.search(query or ""):
            return months

    return default


def add_months(day: datetime.date, months: int) -> datetime.date:
    """``day`` shifted by ``months``, rolling the year over correctly.

    The previous inline arithmetic clamped with min(month + 3, 12), so a
    forecast requested in November produced a one-month window.
    """
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day.day, last_day))
