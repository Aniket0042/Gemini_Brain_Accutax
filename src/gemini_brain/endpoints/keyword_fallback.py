"""
keyword_fallback.py — Hardcoded keyword mapping rules for API endpoints.

Extracted from gemini_brain_adapter.py lines 247-292 (_keyword_endpoint_fallback).
Preserves exact behavior, patterns, logic, and known bug behavior as mandated.
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional
from gemini_brain.config.settings import settings


from gemini_brain.router.rules import ROUTING_RULES


def keyword_endpoint_fallback(
    query: str,
    org_id: int,
    today: datetime.date,
    user_id: str = "",
) -> Optional[Dict]:
    """Declarative keyword fallback mapping derived from unified ROUTING_RULES.

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

    for r in ROUTING_RULES:
        if not r.keyword_triggers:
            continue

        if any(k in q for k in r.keyword_triggers):
            if r.endpoint == "/report/cash-forecast":
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
            elif r.endpoint in ("/income/total", "/expense/total"):
                return {
                    "endpoint": r.endpoint,
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
            elif r.endpoint in ("/report/profit-loss", "/report/balance-sheet", "/report/cash-flow"):
                return {
                    "endpoint": r.endpoint,
                    "method": "GET",
                    "path_params": {},
                    "query_params": {
                        "organization_id": str(org_id),
                        "start_date": f"{y_str}-01-01",
                        "end_date": td,
                    },
                    "reason": "keyword_fallback",
                }
            else:
                return {
                    "endpoint": r.endpoint,
                    "method": "GET",
                    "path_params": {},
                    "query_params": {
                        "organization_id": str(org_id),
                    },
                    "reason": "keyword_fallback",
                }

    return None

