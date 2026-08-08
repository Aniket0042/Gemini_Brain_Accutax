"""
fast_path.py — Fast-path regex matching to bypass LLM tool-calling for canonical questions.

Extracted from model_arena/backend/cost_optimizer.py lines 200-331.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.sql_fallback.cost_optimizer import (
    get_cached_result,
    set_cached_result,
)

logger = logging.getLogger("gemini_brain.sql_fallback.fast_path")


def _safe_int(m: Any, group: int, default: int = 10) -> int:
    try:
        v = m.group(group)
        return int(v) if v else default
    except (IndexError, AttributeError, TypeError, ValueError):
        return default


_FAST_PATH: List[Tuple[Any, str, Any]] = [
    (
        re.compile(r"top\s+(\d+)\s+customers?", re.I),
        "top_customers",
        lambda m, oid: {"limit": _safe_int(m, 1, 10), "organization_id": oid},
    ),
    (
        re.compile(r"top\s+(\d+)\s+(?:vendors?|suppliers?)", re.I),
        "top_vendors",
        lambda m, oid: {"limit": _safe_int(m, 1, 10), "organization_id": oid},
    ),
    (
        re.compile(
            r"(?:bank|cash)\s+balance"
            r"|how\s+much\s+(?:cash|money)\s+(?:do\s+we\s+have|in\s+(?:the\s+)?bank)",
            re.I,
        ),
        "bank_balances",
        lambda m, oid: {"organization_id": oid},
    ),
    (
        re.compile(
            r"(?:ar|accounts?\s+receivable)\s+aging|aged\s+receivables?", re.I
        ),
        "ar_aging",
        lambda m, oid: {"organization_id": oid},
    ),
    (
        re.compile(
            r"(?:ap|accounts?\s+payable)\s+aging|aged\s+payables?", re.I
        ),
        "ap_aging",
        lambda m, oid: {"organization_id": oid},
    ),
    (
        re.compile(
            r"overdue\s+invoices?|unpaid\s+invoices?|late\s+invoices?", re.I
        ),
        "overdue_invoices",
        lambda m, oid: {"organization_id": oid, "limit": 20},
    ),
    (
        re.compile(
            r"top\s+(\d+)\s+defaulters?"
            r"|(?:customers?|companies?)\s+who\s+owe(?:\s+most)?",
            re.I,
        ),
        "customer_overdue_summary",
        lambda m, oid: {"limit": _safe_int(m, 1, 10), "organization_id": oid},
    ),
    (
        re.compile(
            r"invoice\s+status\s+summary"
            r"|how\s+many\s+invoices\s+(?:are\s+)?(?:paid|pending|overdue)",
            re.I,
        ),
        "invoice_status_summary",
        lambda m, oid: {"organization_id": oid},
    ),
    (
        re.compile(
            r"expense(?:s)?\s+by\s+categor|(?:expense|spend)\s+breakdown", re.I
        ),
        "expense_by_category",
        lambda m, oid: {"organization_id": oid},
    ),
    (
        re.compile(
            r"(?:monthly\s+)?revenue\s+trend|revenue\s+by\s+month", re.I
        ),
        "monthly_revenue_trend",
        lambda m, oid: {"months": 12, "organization_id": oid},
    ),
    (
        re.compile(
            r"chart\s+of\s+accounts?|list\s+(?:all\s+)?accounts?", re.I
        ),
        "chart_of_accounts",
        lambda m, oid: {"organization_id": oid},
    ),
]


def try_fast_path(
    question: str,
    org_id: int,
    agent_handlers: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    """Short-circuit the LLM tool-calling loop for canonical questions."""
    handler = agent_handlers.get("finance_agent")
    if not handler:
        return None

    for pattern, task, builder in _FAST_PATH:
        m = pattern.search(question)
        if not m:
            continue

        try:
            params = builder(m, org_id)
        except Exception:
            params = {"organization_id": org_id}

        cached = get_cached_result(org_id, task, params)
        if cached is not None:
            logger.info("Fast-path CACHE HIT: task=%s", task)
            return cached, task, params

        try:
            result = handler(task, params)
        except Exception as e:
            logger.warning("Fast-path %s failed: %s — falling back to LLM", task, e)
            return None

        if result.get("success"):
            set_cached_result(org_id, task, params, result)

        logger.info("Fast-path HIT: task=%s  q=%.60s", task, question)
        return result, task, params

    return None
