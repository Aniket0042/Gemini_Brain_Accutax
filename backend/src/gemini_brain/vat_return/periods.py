"""UAE VAT tax periods and the due date of a return.

A tax period is a month or a quarter (Executive Regulation, Article 55, names the quarterly periods by the month in which
they end: January, February or March, and every three months after). The FTA sets each registrant's period on the
registration certificate; the books may say monthly or quarterly, but never which quarter group, so the quarter group is
an assumption the answer states. A return and the tax due are due by the 28th day after the end of the tax period.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Tuple

DUE_DAYS = 28
FREQUENCIES = ("monthly", "quarterly")
#: The month (1, 2 or 3) in which the first quarter of the year ends: 3 is calendar quarters (March, June, ...).
QUARTER_GROUPS = (1, 2, 3)
DEFAULT_QUARTER_GROUP = 3


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _add_months(year: int, month: int, n: int) -> Tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def period_containing(day: dt.date, frequency: str = "quarterly", quarter_group: int = DEFAULT_QUARTER_GROUP) -> Tuple[dt.date, dt.date]:
    """(first day, last day) of the tax period that holds `day`."""
    if frequency == "monthly":
        return day.replace(day=1), _month_end(day.year, day.month)
    if quarter_group not in QUARTER_GROUPS:
        raise ValueError(f"quarter_group must be one of {QUARTER_GROUPS}")
    # Quarters end in months quarter_group, +3, +6, +9. The month index of `day` within its quarter decides the end.
    offset = (day.month - quarter_group) % 3          # 0 when `day` is in a quarter's last month
    end_year, end_month = _add_months(day.year, day.month, (3 - offset) % 3)
    start_year, start_month = _add_months(end_year, end_month, -2)
    return dt.date(start_year, start_month, 1), _month_end(end_year, end_month)


def previous_period(start: dt.date, frequency: str = "quarterly", quarter_group: int = DEFAULT_QUARTER_GROUP) -> Tuple[dt.date, dt.date]:
    return period_containing(start - dt.timedelta(days=1), frequency, quarter_group)


def last_complete_period(today: dt.date, frequency: str = "quarterly",
                         quarter_group: int = DEFAULT_QUARTER_GROUP) -> Tuple[dt.date, dt.date]:
    """The latest tax period that has ended before today."""
    start, _ = period_containing(today, frequency, quarter_group)
    return previous_period(start, frequency, quarter_group)


def due_date(period_end: dt.date) -> dt.date:
    """The day by which the return is filed and the tax paid: the 28th day after the end of the tax period."""
    return period_end + dt.timedelta(days=DUE_DAYS)
