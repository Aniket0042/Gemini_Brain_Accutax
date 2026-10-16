"""Administrative penalties for a late VAT return, late payment and a voluntary disclosure, calculated by code.

The rules are Cabinet Decision No. 40 of 2017 as amended by Cabinet Decision No. 129 of 2025, in force from 14 April 2026
(Table 3), read from the FTA's text in the knowledge base:
  item 8   failure to submit the tax return on time: AED 1,000 the first time, AED 2,000 when repeated within 24 months
  item 9   failure to settle the payable tax on time: a monthly penalty of 14% per annum, for each month or part thereof,
           on the unsettled payable tax, from the day after the due date and on the same date each month after that
  item 11  a voluntary disclosure of an error in a return: a monthly penalty of 1% of the tax difference, for each month or
           part thereof, from the day after the return's due date until the disclosure is submitted
A penalty that falls on a day a month does not have (the 31st) falls on the first day of the next month (Cabinet
Decision No. 49 of 2021). The calculator works only for a due date on or after 14 April 2026: earlier due dates are
under the earlier rules, which it does not apply.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Any, Dict, List

NEW_RULES_FROM = dt.date(2026, 4, 14)
LATE_RETURN_FIRST, LATE_RETURN_REPEAT = 1_000.0, 2_000.0
LATE_PAYMENT_ANNUAL_RATE = 0.14
DISCLOSURE_MONTHLY_RATE = 0.01
SOURCE = "Cabinet Decision No. 40 of 2017 as amended by Cabinet Decision No. 129 of 2025 (in force from 14 April 2026), Table 3"


def _same_date_months_later(first: dt.date, months: int) -> dt.date:
    """`first` moved by whole months; a day the month lacks becomes the first day of the next month."""
    total = first.year * 12 + (first.month - 1) + months
    year, month = total // 12, total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    if first.day <= last:
        return dt.date(year, month, first.day)
    return dt.date(year, month, last) + dt.timedelta(days=1)


def penalty_dates(due: dt.date, until: dt.date) -> List[dt.date]:
    """The days a monthly penalty is imposed: the day after the due date, then the same date each month, up to `until`."""
    first = due + dt.timedelta(days=1)
    out: List[dt.date] = []
    n = 0
    while True:
        day = _same_date_months_later(first, n)
        if day > until:
            return out
        out.append(day)
        n += 1


def _before_new_rules(due: dt.date) -> Dict[str, Any]:
    return {"calculated": False, "reason": f"The due date {due.isoformat()} is before 14 April 2026, when the current "
            "penalty rules came into force. Earlier due dates are under the earlier rules, which this calculator does not "
            "apply."}


def late_payment(amount: float, due: dt.date, paid_on: dt.date) -> Dict[str, Any]:
    """The late-payment penalty on `amount` of payable tax due on `due` and paid (or still unpaid) on `paid_on`."""
    if due < NEW_RULES_FROM:
        return _before_new_rules(due)
    dates = penalty_dates(due, paid_on)
    monthly = round(amount * LATE_PAYMENT_ANNUAL_RATE / 12, 2)
    return {"calculated": True, "kind": "late_payment", "amount": round(amount, 2), "due_date": due.isoformat(),
            "until": paid_on.isoformat(), "months": len(dates), "monthly_penalty": monthly,
            "penalty": round(monthly * len(dates), 2), "penalty_dates": [d.isoformat() for d in dates[:24]],
            "rule": "14% per annum, for each month or part of a month, on the unsettled tax (item 9)", "source": SOURCE}


def late_return(repeated_within_24_months: bool = False) -> Dict[str, Any]:
    penalty = LATE_RETURN_REPEAT if repeated_within_24_months else LATE_RETURN_FIRST
    return {"calculated": True, "kind": "late_return", "penalty": penalty,
            "rule": "AED 1,000 the first time, AED 2,000 when repeated within 24 months (item 8)", "source": SOURCE}


def voluntary_disclosure(difference: float, return_due: dt.date, submitted_on: dt.date) -> Dict[str, Any]:
    """The penalty on a voluntary disclosure that corrects a return by `difference` of tax."""
    if return_due < NEW_RULES_FROM:
        return _before_new_rules(return_due)
    dates = penalty_dates(return_due, submitted_on)
    monthly = round(abs(difference) * DISCLOSURE_MONTHLY_RATE, 2)
    return {"calculated": True, "kind": "voluntary_disclosure", "difference": round(abs(difference), 2),
            "return_due_date": return_due.isoformat(), "until": submitted_on.isoformat(), "months": len(dates),
            "monthly_penalty": monthly, "penalty": round(monthly * len(dates), 2),
            "rule": "1% of the tax difference, for each month or part of a month, until the disclosure (item 11)",
            "source": SOURCE}
