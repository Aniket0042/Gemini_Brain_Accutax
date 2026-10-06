"""
periods.py — Turn a period request into an absolute date range.

The model picks a preset; code decides the dates in the reporting time zone,
so "this year" never reaches into future-dated documents.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

PRESETS = ("today", "this_month", "last_month", "this_quarter", "last_quarter",
           "ytd", "last_year", "last_12_months")


def today_in(tz: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(tz)).date()


def parse_date(value: Any) -> Optional[dt.date]:
    """An ISO date (time part ignored); None when absent. Raises ValueError when malformed."""
    if not value:
        return None
    return dt.date.fromisoformat(str(value).strip()[:10])


def _month_start(d: dt.date, back: int = 0) -> dt.date:
    idx = d.year * 12 + d.month - 1 - back
    return dt.date(idx // 12, idx % 12 + 1, 1)


def resolve(period: Optional[Dict[str, Any]], today: dt.date) -> Tuple[dt.date, dt.date]:
    """(start, end), both inclusive. No period means year-to-date."""
    period = period or {"preset": "ytd"}
    preset = period.get("preset")
    quarter_start = dt.date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    if preset == "today":
        return today, today
    if preset == "this_month":
        return _month_start(today), today
    if preset == "last_month":
        return _month_start(today, 1), _month_start(today) - dt.timedelta(days=1)
    if preset == "this_quarter":
        return quarter_start, today
    if preset == "last_quarter":
        return _month_start(quarter_start, 3), quarter_start - dt.timedelta(days=1)
    if preset == "ytd":
        return dt.date(today.year, 1, 1), today
    if preset == "last_year":
        return dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31)
    if preset == "last_12_months":
        return _month_start(today, 11), today
    if preset is None:
        start, end = parse_date(period.get("start")), parse_date(period.get("end"))
        if start and end:
            if start > end:
                raise ValueError("period start is after its end")
            return start, end
    raise ValueError(f"unknown period {period!r}")
