"""
keyword_fallback.py — Hardcoded keyword mapping rules for API endpoints.

Extracted from gemini_brain_adapter.py lines 247-292 (_keyword_endpoint_fallback).
Preserves exact behavior, patterns, logic, and known bug behavior as mandated.
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional
from gemini_brain.config.settings import settings


def keyword_endpoint_fallback(
    query: str,
    org_id: int,
    today: datetime.date,
    user_id: str = "",
) -> Optional[Dict]:
    """Hardcoded keyword fallback mapping for endpoints Gemini fails to select.

    Parameters
    ----------
    query : str
        User's natural-language question.
    org_id : int
        Organization ID.
    today : datetime.date
        Current date.
    user_id : str
        Default user ID string.

    Returns
    -------
    Optional[Dict]
        Endpoint selection dictionary or None if no match.
    """
    q = query.lower()
    y_str = str(today.year)
    td = today.isoformat()
    uid = user_id or settings.accutax_user_id

    # Cash forecast / projection
    if any(
        k in q
        for k in [
            "cash forecast",
            "forecast cash",
            "cash flow forecast",
            "cash projection",
            "projected cash",
            "next.*month.*cash",
            "forecast.*cash",
            "cash.*next",
        ]
    ):
        return {
            "endpoint": "/report/cash-forecast",
            "method": "GET",
            "path_params": {},
            "query_params": {
                "organization_id": str(org_id),
                "start_date": td,
                "end_date": today.replace(
                    year=today.year, month=min(today.month + 3, 12), day=1
                ).isoformat(),
            },
            "reason": "keyword_fallback",
        }

    # Total income / sales
    if any(
        k in q
        for k in [
            "total sales",
            "total revenue",
            "total income",
            "how much.*income",
            "how much.*revenue",
            "how much.*sales",
        ]
    ):
        return {
            "endpoint": "/income/total",
            "method": "GET",
            "path_params": {},
            "query_params": {
                "user_id": str(uid),
                "filter_year": y_str,
                "filter_type": "YEARLY",
                "organization_id": str(org_id),
            },
            "reason": "keyword_fallback",
        }

    # Total expenses
    if any(
        k in q
        for k in [
            "total expenses",
            "total expense",
            "total spending",
            "total bills",
            "how much.*expense",
            "how much.*spend",
        ]
    ):
        return {
            "endpoint": "/expense/total",
            "method": "GET",
            "path_params": {},
            "query_params": {
                "user_id": str(uid),
                "filter_year": y_str,
                "filter_type": "YEARLY",
                "organization_id": str(org_id),
            },
            "reason": "keyword_fallback",
        }

    return None
