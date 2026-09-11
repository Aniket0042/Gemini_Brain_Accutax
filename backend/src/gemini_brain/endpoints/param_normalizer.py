"""
param_normalizer.py — Parameter normalization for specific API endpoints.

Extracted from gemini_brain_adapter.py lines 229-245 (_normalize_endpoint_params).
Fixes parameters for endpoints that Gemini consistently gets wrong (e.g., /income/total and /expense/total).
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional
from gemini_brain.config.settings import settings
from gemini_brain.router import dates

_VALID_FILTER_TYPES = {"YEARLY", "QUARTERLY", "MONTHLY"}


def _infer_filter_type(window: dates.Window) -> str:
    """Map a resolved date window to the closest filter_type /income|expense/total accepts.

    The API only understands whole YEARLY/QUARTERLY/MONTHLY buckets, not arbitrary
    date ranges, so this snaps to the nearest bucket size the window implies.
    """
    months_spanned = (
        (window.date_to.year - window.date_from.year) * 12
        + (window.date_to.month - window.date_from.month)
        + 1
    )
    if months_spanned <= 1:
        return "MONTHLY"
    if months_spanned <= 3:
        return "QUARTERLY"
    return "YEARLY"


def normalize_endpoint_params(
    sel: Dict,
    org_id: int,
    today: datetime.date,
    user_id: str = "",
    window: Optional[dates.Window] = None,
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
    window : Optional[dates.Window]
        The date range already resolved for this query, when the caller has one
        (e.g. the fast router). Used only to fill in filter_year/filter_type when
        the caller didn't already supply them — never overrides an explicit value.

    Returns
    -------
    Dict
        Updated selection dictionary with normalized query_params.
    """
    uid = user_id or settings.accutax_user_id
    ep = sel.get("endpoint", "")
    qp = dict(sel.get("query_params", {}))

    if ep in ("/income/total", "/expense/total"):
        # These endpoints REQUIRE user_id + filter_year + filter_type, NOT start/end dates.
        qp.pop("start_date", None)
        qp.pop("end_date", None)
        qp["user_id"] = str(qp.get("user_id") or uid)
        qp["organization_id"] = str(org_id)

        if qp.get("filter_type") not in _VALID_FILTER_TYPES:
            qp.pop("filter_type", None)

        if window is not None:
            qp.setdefault("filter_year", str(window.date_to.year))
            qp.setdefault("filter_type", _infer_filter_type(window))
        else:
            qp.setdefault("filter_year", str(today.year))
            qp.setdefault("filter_type", "YEARLY")

        sel = {**sel, "query_params": qp}

    return sel
