"""
param_normalizer.py — Parameter normalization for specific API endpoints.

Extracted from gemini_brain_adapter.py lines 229-245 (_normalize_endpoint_params).
Fixes parameters for endpoints that Gemini consistently gets wrong (e.g., /income/total and /expense/total).
"""
from __future__ import annotations

import datetime
from typing import Dict
from gemini_brain.config.settings import settings


def normalize_endpoint_params(
    sel: Dict,
    org_id: int,
    today: datetime.date,
    user_id: str = "",
) -> Dict:
    """Fix query parameters for endpoints Gemini consistently formats incorrectly.

    Parameters
    ----------
    sel : Dict
        Selection dictionary returned from Gemini or keyword fallback.
    org_id : int
        Organization ID.
    today : datetime.date
        Today's date.
    user_id : str
        Default user ID string.

    Returns
    -------
    Dict
        Updated selection dictionary with normalized query_params.
    """
    uid = user_id or settings.accutax_user_id
    ep = sel.get("endpoint", "")
    qp = dict(sel.get("query_params", {}))
    y_str = str(today.year)

    if ep in ("/income/total", "/expense/total"):
        # These endpoints REQUIRE user_id + filter_year + filter_type, NOT start/end dates
        qp = {
            "user_id": str(uid),
            "filter_year": y_str,
            "filter_type": "YEARLY",
            "organization_id": str(org_id),
        }
        sel = {**sel, "query_params": qp}

    return sel
