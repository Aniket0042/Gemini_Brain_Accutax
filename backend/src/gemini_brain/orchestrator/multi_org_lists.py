"""
multi_org_lists.py — Read the filters of a multi-org invoice or bill list question.

"Top 10 largest bills overall", "list every unpaid invoice older than 180 days
per org", "bills over 50,000 AED this year": the kind of document, its status,
amount and age limits, the sort and the row count are read here in code and
sent to rpt_document_list, which applies them in SQL for each organization.
Before, the newest 20 rows were fetched and a model was left to filter them.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from gemini_brain.router import dates


def _p(expr: str) -> "re.Pattern[str]":
    return re.compile(expr, re.IGNORECASE)


_BILL = _p(r"\b(bills?|payables?|purchases?|purchase\s+invoices?|supplier\s+invoices?|vendor\s+invoices?)\b")
_INVOICE = _p(r"\b(invoices?|receivables?|sales\s+invoices?)\b")

_OPEN = _p(r"\b(unpaid|outstanding|open|pending|not\s+(?:yet\s+)?paid)\b|\bdue\b(?!\s+dates?)")
_OVERDUE = _p(r"\b(overdue|past\s+due|late)\b")
_PAID = _p(r"\b(fully\s+)?paid\s+(invoices?|bills?)\b|\b(invoices?|bills?)\s+(that\s+(are|were)\s+)?paid\b")
_CANCELLED = _p(r"\b(cancell?ed|voided)\b")
_ALL_STATUSES = _p(r"\b(including|with)\s+(cancell?ed|voided)\b|\ball\s+statuses\b")

_UNITS = r"(?!\s*(?:days?|weeks?|months?|years?|organi[sz]ations?|orgs?|entit(?:y|ies)|compan(?:y|ies)|times?))"
_NUMBER = r"(?:aed|usd|inr|eur|gbp|\$|₹)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|m|thousand|million|lakh|lakhs|crore|crores)?\b"
_MIN_AMOUNT = _p(r"\b(?:more\s+than|over|above|greater\s+than|exceeding|at\s+least|minimum\s+of|>=?)\s*" + _NUMBER + _UNITS)
_MAX_AMOUNT = _p(r"\b(?:less\s+than|under|below|at\s+most|up\s+to|<=?)\s*" + _NUMBER + _UNITS)
_AMOUNT_RANGE = _p(r"\b(?:between|from)\s+(?:aed|usd|inr|\$)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|m)?\s+(?:and|to)\s+"
                   r"(?:aed|usd|inr|\$)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|m)?\b")
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7}

_AGE = _p(r"\b(?:older\s+than|more\s+than|over|at\s+least)\s+(\d+)\s*(days?|weeks?|months?|years?)(?:\s+old)?\b")
_OVERDUE_BY = _p(r"\b(?:overdue|past\s+due|late)\s+(?:by\s+)?(?:more\s+than|over|at\s+least)?\s*(\d+)\s*(days?|weeks?|months?)\b"
                 r"|\b(\d+)\+?\s*(days?|weeks?|months?)\s+(?:overdue|past\s+due|late)\b")
_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30, "year": 365, "years": 365}

_LARGEST = _p(r"\b(largest|biggest|highest|top|most\s+expensive|major)\b")
_SMALLEST = _p(r"\b(smallest|lowest|least)\b")
_LATEST = _p(r"\b(latest|recent|newest|last)\b(?!\s+(?:year|quarter|month|week))")
_OLDEST = _p(r"\b(oldest|earliest)\b")


def _amount(match: Optional["re.Match[str]"]) -> Optional[float]:
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value * _SCALE.get((match.group(2) or "").lower(), 1)


def _days(number: str, unit: str) -> int:
    return int(number) * _DAYS.get(unit.lower(), 1)


_SUPPLIER_INVOICE = _p(r"\b(supplier|vendor|purchase)\s+invoices?\b")


def document_kind(question: str) -> Optional[str]:
    """ "bill" or "invoice" when the question is about exactly one of them."""
    q = question or ""
    if _SUPPLIER_INVOICE.search(q):
        return "bill"
    bill, invoice = bool(_BILL.search(q)), bool(_INVOICE.search(q))
    if bill and invoice:
        return None
    return "bill" if bill else "invoice" if invoice else None


def document_filters(question: str) -> Dict[str, Any]:
    """The filters, sort and row count a list question states."""
    from gemini_brain.utils.ranking import extract_explicit_count

    q = question or ""
    params: Dict[str, Any] = {}
    if _ALL_STATUSES.search(q):
        params["status"] = "all"
    elif _CANCELLED.search(q):
        params["status"] = "cancelled"
    elif _PAID.search(q) and not _OPEN.search(q):
        params["status"] = "paid"
    elif _OPEN.search(q) or _OVERDUE.search(q):
        params["status"] = "open"
    overdue_by = _OVERDUE_BY.search(q)
    if overdue_by:
        number, unit = (overdue_by.group(1), overdue_by.group(2)) if overdue_by.group(1) else (overdue_by.group(3), overdue_by.group(4))
        params["overdue_days"] = _days(number, unit)
        params["status"] = "open"
    elif _OVERDUE.search(q):
        params["overdue"] = True
    age = _AGE.search(q)
    if age and not overdue_by:
        params["older_than_days"] = _days(age.group(1), age.group(2))
    low, high = _amount(_MIN_AMOUNT.search(q)), _amount(_MAX_AMOUNT.search(q))
    between = _AMOUNT_RANGE.search(q)
    if between and low is None and high is None:
        from gemini_brain.orchestrator.multi_org_dates import _is_amount_range

        if _is_amount_range(between.group(0)):
            low = float(between.group(1).replace(",", "")) * _SCALE.get((between.group(2) or "").lower(), 1)
            high = float(between.group(3).replace(",", "")) * _SCALE.get((between.group(4) or "").lower(), 1)
    if low is not None:
        params["min_amount"] = low
    if high is not None:
        params["max_amount"] = high
    if _OLDEST.search(q):
        params["order"] = "date_asc"
    elif _LATEST.search(q) and not _LARGEST.search(q):
        params["order"] = "date_desc"
    elif _SMALLEST.search(q):
        params["order"] = "amount_asc"
    else:
        params["order"] = "amount_desc"
    count = extract_explicit_count(q)
    params["limit"] = count if count else 25
    return params


def _window(question: str) -> Optional[Dict[str, str]]:
    from gemini_brain.orchestrator.multi_org_dates import period_phrase, resolve_window

    if not period_phrase(question or ""):
        return None  # all history: an age or status filter is not limited to this year
    start, end = resolve_window(question)
    return {"start_date": start.isoformat(), "end_date": end.isoformat()}


def document_list_selection(question: str) -> Optional[Dict[str, Any]]:
    """The rpt_document_list selection for an invoice or bill list question, or None."""
    kind = document_kind(question)
    if kind is None:
        return None
    params: Dict[str, Any] = {"kind": kind, **document_filters(question)}
    params.update(_window(question) or {})
    return {
        "endpoint": "rpt_document_list",
        "path_params": {},
        "query_params": params,
        "reason": f"Document list: {describe(params)}",
    }


def describe(params: Dict[str, Any]) -> str:
    """The filters in words, for the answer heading, e.g. "unpaid invoices older than 180 days"."""
    kind = "bills" if params.get("kind") == "bill" else "invoices"
    status = {"open": "unpaid ", "paid": "paid ", "cancelled": "cancelled or voided ", "all": ""}.get(
        str(params.get("status") or ""), "")
    parts = [f"{status}{kind}".strip()]
    if params.get("status") in (None, "default"):
        parts.append("(cancelled and voided excluded)")
    if params.get("overdue_days"):
        parts.append(f"overdue by more than {params['overdue_days']} days")
    elif params.get("overdue"):
        parts.append("past their due date")
    if params.get("older_than_days"):
        parts.append(f"dated more than {params['older_than_days']} days ago")
    if params.get("min_amount") is not None:
        parts.append(f"over {params['min_amount']:,.2f}")
    if params.get("max_amount") is not None:
        parts.append(f"under {params['max_amount']:,.2f}")
    if params.get("start_date"):
        parts.append(f"from {params['start_date']} to {params['end_date']}")
    order = {"amount_desc": "largest first", "amount_asc": "smallest first",
             "date_desc": "newest first", "date_asc": "oldest first"}.get(str(params.get("order") or ""), "")
    if order:
        parts.append(order)
    return ", ".join(parts)


# ── Filters on shared vendors / customers ────────────────────────────────────

_EVERY_ORG = _p(r"\b(all|every|each)\s+(?:of\s+(?:the|my|our)\s+)?(?:\w+\s+)?(organi[sz]ations?|orgs?|entit(?:y|ies)|compan(?:y|ies))\b"
                r"|\bcommon\s+to\s+all\b|\bin\s+all\b")


def overlap_filters(question: str) -> Dict[str, Any]:
    """{"every_org": bool, "min_total": float | None} for a shared vendors/customers question."""
    return {"every_org": bool(_EVERY_ORG.search(question or "")),
            "min_total": _amount(_MIN_AMOUNT.search(question or ""))}


# ── Cross-organization matching that no report does ──────────────────────────

#: (pattern, what it asks, why it is not available). Checked for list
#: questions too: the per-org fallback answered these with each org's top
#: vendors, claiming matches it never looked for.
CROSS_ORG_UNSUPPORTED: List[tuple] = [
    (_p(r"\bsame\s+(day|week|month|date)\b"),
     "Matching payments across organizations by date",
     "each organization's records are fetched separately and not matched by date"),
    (_p(r"\bduplicate[sd]?\b.*\b(across|between|in\s+(?:more\s+than\s+one|multiple|different))\b"
        r"|\b(across|between)\b.*\bduplicate[sd]?\b"),
     "Finding duplicates across organizations",
     "documents are not compared across organizations"),
    (_p(r"\b(customers?|clients?)\b.*\balso\b.*\b(vendors?|suppliers?)\b"
        r"|\b(vendors?|suppliers?)\b.*\balso\b.*\b(customers?|clients?)\b"),
     "Matching customers of one organization with vendors of another",
     "customer and vendor lists are only compared within the same role"),
    (_p(r"\binter[\s-]*compan(?:y|ies)\b|\brelated[\s-]+part(?:y|ies)\b|\beliminat\w*\b"),
     "Intercompany and related-party balances",
     "transactions between the selected organizations are not identified or eliminated"),
]


#: List filters rpt_document_list cannot apply. The list is still shown, with a
#: note that it is not limited by them, instead of silently ignoring them.
UNAPPLIED_FILTERS: List[tuple] = [
    (_p(r"\bweekends?\b|\b(saturday|sunday)s?\b|\bholidays?\b"), "posting on weekends or holidays"),
    (_p(r"\bround[\s-]*(sum|amount|number|figure)s?\b"), "round amounts"),
    (_p(r"\b(no|without|missing|blank|empty)\s+(description|narration|memo|notes?|reference)s?\b"),
     "missing descriptions"),
    (_p(r"\b(posted|created|entered|approved)\s+by\b"), "who posted or approved them"),
    (_p(r"\bafter\s+hours\b|\bat\s+night\b"), "posting time"),
]


def unapplied_filters(question: str) -> List[str]:
    """A note per filter in the question that the list does not apply."""
    return [f"Not filtered by {what}: that filter is not available yet, so the list is not limited by it."
            for pattern, what in UNAPPLIED_FILTERS if pattern.search(question or "")]


def unavailable_filters(question: str) -> List[str]:
    """The same filters, for a question with no invoice or bill list to show instead."""
    return [f"Filtering by {what} is not available yet." for pattern, what in UNAPPLIED_FILTERS
            if pattern.search(question or "")]


def cross_org_unsupported(question: str) -> List[str]:
    """One note per cross-organization match the question asks for that cannot be done."""
    return [f"{what} is not available: {why}." for pattern, what, why in CROSS_ORG_UNSUPPORTED
            if pattern.search(question or "")]
