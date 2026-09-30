"""
multi_org_dates.py — The period a multi-organization question covers.

The shared period regex read "FY 2025-26", "since January 2025" and "between
1 Jan 2026 and 31 Mar 2026" as the bare year "2025"/"2026", and did not know
"year to date". "This year" also meant 1 Jan - 31 Dec on one path and
1 Jan - today on another, so the same thread showed Org4's revenue as 16.0M
(including invoices dated in the future) and then 12.18M.

Here explicit ranges are read first, the shared resolver handles the rest, and
every window that contains today ends today: a period is always "to date".
"""
from __future__ import annotations

import calendar
import datetime
import re
from typing import Optional, Tuple

from gemini_brain.router import dates

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

#: One date as people write it: 2026-01-31, 31/01/2026, 31 Jan 2026, Jan 31, 2026, January 2026, 2026.
_DATE = (r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+\d{4}"
         r"|" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|" + _MONTH + r",?\s+\d{4}|(?:19|20)\d{2})")

_BETWEEN = re.compile(r"\b(?:between|from)\s+(" + _DATE + r")\s+(?:and|to|till|until|-|–)\s+(" + _DATE + r")\b", re.I)
_SINCE = re.compile(r"\b(?:since|from|starting|after)\s+(" + _DATE + r")\b", re.I)
_FY = re.compile(r"\bFY\s*'?(\d{2}|\d{4})(?:\s*[-/–]\s*'?(\d{2}|\d{4}))?\b", re.I)
_YTD = re.compile(r"\b(?:ytd|year[\s-]+to[\s-]+date)\b", re.I)
_MONTH_YEAR = re.compile(r"\b(?:in\s+|for\s+)?(" + _MONTH + r")\s+(\d{4})\b", re.I)
_NEXT = re.compile(r"\bnext\s+(month|quarter|year)\b", re.I)
#: "between 2000 and 5000 AED" is an amount range, not the year 2000.
_NUMBER_RANGE = re.compile(r"\b(?:between|from)\s+([\d,]+(?:\.\d+)?)\s*(?:k|m)?\s+(?:and|to)\s+([\d,]+(?:\.\d+)?)", re.I)


def _is_amount_range(text: str) -> bool:
    m = _NUMBER_RANGE.search(text or "")
    if not m:
        return False
    values = [float(v.replace(",", "")) for v in m.groups()]
    return any(not (1900 <= v <= 2100 and v == int(v)) for v in values)


def _parse(text: str, end: bool) -> Optional[datetime.date]:
    """One written date; a month or year alone is its first day, or its last when `end`."""
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text.strip().lower().replace(",", ""))
    try:
        if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", t):
            return datetime.date.fromisoformat("-".join(p.zfill(2) for p in t.split("-")))
        m = re.fullmatch(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", t)
        if m:  # day first, as written in the UAE and India
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", t) or re.fullmatch(r"([a-z]+)\s+(\d{1,2})\s+(\d{4})", t)
        if m:
            a, b, year = m.groups()
            day, month = (a, b) if a.isdigit() else (b, a)
            return datetime.date(int(year), _MONTHS[month], int(day))
        m = re.fullmatch(r"([a-z]+)\s+(\d{4})", t)
        if m:
            year, month = int(m.group(2)), _MONTHS[m.group(1)]
            return datetime.date(year, month, calendar.monthrange(year, month)[1] if end else 1)
        if re.fullmatch(r"\d{4}", t):
            return datetime.date(int(t), 12, 31) if end else datetime.date(int(t), 1, 1)
    except (KeyError, ValueError):
        return None
    return None


def _full_year(two_or_four: str) -> int:
    value = int(two_or_four)
    return value + 2000 if value < 100 else value


def period_phrase(text: str) -> Optional[str]:
    """The words in `text` that state its period, or None."""
    for pattern in (_BETWEEN, _FY, _SINCE, _YTD, _NEXT):
        m = pattern.search(text or "")
        if m and not (pattern is _BETWEEN and _is_amount_range(m.group(0))):
            return m.group(0)
    from gemini_brain.router.fast_router import _extract_period_phrase

    phrase = _extract_period_phrase(text or "")
    if phrase and re.fullmatch(r"\d{4}", phrase.strip()) and _is_amount_range(text):
        return None
    if phrase and re.fullmatch(r"\d{4}", phrase.strip()):
        # "March 2025" is a month, not the year 2025.
        m = _MONTH_YEAR.search(text or "")
        if m and m.group(2) == phrase.strip():
            return f"{m.group(1)} {m.group(2)}"
    return phrase


def _explicit(text: str) -> Optional[Tuple[datetime.date, datetime.date]]:
    today = dates.today()
    m = _BETWEEN.search(text)
    if m and not _is_amount_range(m.group(0)):
        start, end = _parse(m.group(1), end=False), _parse(m.group(2), end=True)
        if start and end:
            return start, end
    m = _FY.search(text)
    if m:
        first = _full_year(m.group(1))
        if m.group(2):
            # "FY 2025-26": April to March.
            return datetime.date(first, 4, 1), datetime.date(first + 1, 3, 31)
        return datetime.date(first, 1, 1), datetime.date(first, 12, 31)
    m = _SINCE.search(text)
    if m:
        start = _parse(m.group(1), end=False)
        if start:
            return start, today
    if _YTD.search(text):
        return today.replace(month=1, day=1), today
    m = _NEXT.search(text)
    if m:
        # A future period: nothing is recorded yet, and the answer says so.
        unit = m.group(1).lower()
        if unit == "year":
            return datetime.date(today.year + 1, 1, 1), datetime.date(today.year + 1, 12, 31)
        if unit == "quarter":
            q_start_month = 3 * ((today.month - 1) // 3) + 4
            year = today.year + (q_start_month > 12)
            q_start_month = (q_start_month - 1) % 12 + 1
            end_month = q_start_month + 2
            return (datetime.date(year, q_start_month, 1),
                    datetime.date(year, end_month, calendar.monthrange(year, end_month)[1]))
        year = today.year + (today.month == 12)
        month = today.month % 12 + 1
        return datetime.date(year, month, 1), datetime.date(year, month, calendar.monthrange(year, month)[1])
    m = _MONTH_YEAR.search(text)
    if m:
        start = _parse(f"{m.group(1)} {m.group(2)}", end=False)
        end = _parse(f"{m.group(1)} {m.group(2)}", end=True)
        if start and end:
            return start, end
    return None


def resolve_window(text: str) -> Tuple[datetime.date, datetime.date]:
    """(start, end) for the question's period, never ending after today when it includes today.

    A reversed range ("between 31 Mar and 1 Jan") is put in order. No period
    means this year to date.
    """
    today = dates.today()
    window = _explicit(text or "")
    if window is None:
        from gemini_brain.router.fast_router import _extract_period_phrase

        phrase = None if _is_amount_range(text or "") else _extract_period_phrase(text or "")
        resolved = dates.resolve(phrase)
        window = (resolved.date_from, resolved.date_to)
    start, end = window
    if start > end:
        start, end = end, start
    if start <= today < end:
        end = today  # "this year" is the year so far: future-dated documents are not counted yet
    return start, end
