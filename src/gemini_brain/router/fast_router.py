"""
fast_router.py — Deterministic regex-based router for common financial queries.

Runs before any LLM calls (zero Gemini Flash latency on matching queries).
"""
from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Tuple

from gemini_brain.config.settings import settings
from gemini_brain.endpoints.param_normalizer import normalize_endpoint_params
from gemini_brain.observability.metrics import METRICS
from gemini_brain.router import dates

logger = logging.getLogger("gemini_brain.router.fast_router")

# Regex to detect date/period phrases in the query
PERIOD_REGEX = re.compile(
    r"\b(this|last|previous|current)\s+(month|quarter|year)\b|"
    r"\b(ytd|mtd|qtd)\b|"
    r"\blast\s+(\d+)\s+(?:months?|days?)\b|"
    r"\b(20\d{2})\b|"
    r"\bq([1-4])(?:\s*(20\d{2}))?\b|"
    r"\bquarter\s*[1-4]\b",
    re.IGNORECASE,
)

FOLLOW_UP_PERIOD_REGEX = re.compile(
    r"^(?:what\s+about|and\s+for|how\s+about|show\s+for|and\s+how\s+does\s+that\s+compare\s+to|what\s+of)\s+([a-z0-9\s]+?)\??$",
    re.IGNORECASE,
)

TASK_TO_ENDPOINT = {
    "profit_loss": "/report/profit-loss",
    "profit_and_loss": "/report/profit-loss",
    "balance_sheet": "/report/balance-sheet",
    "cash_flow": "/report/cash-flow",
    "cash_forecast": "/report/cash-forecast",
    "income_total": "/income/total",
    "expense_total": "/expense/total",
    "sales_by_customer": "/report/sales-by-customer",
    "top_customers": "/report/sales-by-customer",
    "top_vendors": "/report/expense-by-category",
    "expense_by_category": "/report/expense-by-category",
    "ar_aging": "/report/ar-aging-summary",
    "ar_aging_summary": "/report/ar-aging-summary",
    "ap_aging": "/report/ap-aging-summary",
    "ap_aging_summary": "/report/ap-aging-summary",
    "customer_balance_summary": "/report/customer-balance-summary",
    "bank_balances": "/bank/manual/accounts",
}


@dataclass(frozen=True)
class FastRouteResult:
    """Represents a matched API endpoint call generated deterministically."""
    endpoint: str
    method: str = "GET"
    path_params: Dict[str, Any] = field(default_factory=dict)
    query_params: Dict[str, Any] = field(default_factory=dict)
    intent: int = 4
    rule_name: str = ""
    reason: str = "fast_router_regex_match"

    def to_selection_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "method": self.method,
            "path_params": self.path_params,
            "query_params": self.query_params,
            "reason": self.reason,
            "rule_name": self.rule_name,
            "intent": self.intent,
        }


def _extract_period_phrase(query: str) -> Optional[str]:
    """Extract a recognized period phrase from the user query."""
    m = PERIOD_REGEX.search(query)
    if m:
        return m.group(0).strip()
    return None


from gemini_brain.router.rules import get_fast_router_rules

# Ordered Regex Routing Table generated from consolidated rules.py
FAST_ROUTER_RULES: List[Tuple[Pattern, str, str, int]] = get_fast_router_rules()


CONCEPT_GUARD = re.compile(
    r"(what\s+is\s+(the\s+)?difference|difference\s+between|explain\s+(how|what|why)|define\s+|what\s+does\s+|"
    r"how\s+(do|can|should)\s+i\s+(record|create|make|post|file|add|log|enter)|"
    r"what(?:'s|\s+is)\s+the\s+best\s+way\s+to\s+(record|create|add|make|post|file|log|enter)|"
    r"where\s+do\s+i\s+|"
    r"what\s+(is|are)\s+(a\s+|an\s+)?(accounts?\s+receivable|accounts?\s+payable|vat|trn|depreciation|accrual|debit|credit|journal\s+entry))\b",
    re.IGNORECASE,
)


def _build_params_for_endpoint(
    endpoint: str,
    organization_id: int,
    uid: Any,
    window: dates.Window,
    clean_q: str,
    today_date: datetime.date,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"organization_id": organization_id}

    if endpoint == "/expense/total":
        # filter_year/filter_type are derived from `window` by normalize_endpoint_params below.
        # rpt_income_total is NOT here: it's a DB report taking start_date/end_date, same as
        # the branch below, not the REST endpoint's filter_year/filter_type contract.
        params = {
            "organization_id": organization_id,
            "user_id": str(uid),
        }
    elif endpoint in ("rpt_income_total", "/report/profit-loss", "/report/cash-flow", "/report/sales-by-customer", "/report/expense-by-category"):
        params = {
            "organization_id": organization_id,
            "start_date": window.date_from.isoformat(),
            "end_date": window.date_to.isoformat(),
        }
    elif endpoint in ("/report/balance-sheet", "/report/ar-aging-summary", "/report/ap-aging-summary"):
        params = {
            "organization_id": organization_id,
            "as_of_date": window.date_to.isoformat(),
        }
    elif endpoint == "/report/cash-forecast":
        params = {
            "organization_id": organization_id,
            "months": 6,
        }
    elif endpoint == "/report/customer-balance-summary":
        params = {
            "organization_id": organization_id,
        }
    elif endpoint in ("/bank/manual/accounts", "/bank/manual/unassigned-transactions"):
        params = {
            "organization_id": organization_id,
        }
    elif endpoint == "/income/list":
        status = "unpaid" if "unpaid" in clean_q.lower() else ("paid" if "paid" in clean_q.lower() else "all")
        params = {
            "userId": int(uid) if str(uid).isdigit() else 18,
            "limit": 20,
            "start_date": window.date_from.isoformat(),
            "end_date": window.date_to.isoformat(),
        }
        if status != "all":
            params["status"] = status
    elif endpoint == "/expense/list":
        status = "unpaid" if "unpaid" in clean_q.lower() else ("paid" if "paid" in clean_q.lower() else "all")
        params = {
            "userId": int(uid) if str(uid).isdigit() else 18,
            "limit": 20,
            "start_date": window.date_from.isoformat(),
            "end_date": window.date_to.isoformat(),
        }
        if status != "all":
            params["status"] = status
    elif endpoint == "/item/list":
        params = {
            "user_id": str(uid),
            "limit": 20,
        }
        if "price" in clean_q.lower():
            params["sort_by"] = "price"
            params["order"] = "desc"
    elif endpoint in ("fn_project_expense_rollup", "fn_inventory_movement", "fn_gl_profitability"):
        params = {
            "organization_id": organization_id,
            "start_date": window.date_from.isoformat(),
            "end_date": window.date_to.isoformat(),
        }

    raw_sel = {
        "endpoint": endpoint,
        "method": "GET",
        "path_params": {},
        "query_params": params,
    }
    return normalize_endpoint_params(raw_sel, organization_id, today_date, user_id=uid, window=window)


def fast_route(
    query: str,
    organization_id: int,
    user_id: Optional[str] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> Optional[FastRouteResult]:
    """Deterministically route recognizable queries to endpoints in 0 LLM calls.

    Parameters
    ----------
    query : str
        The raw user query text.
    organization_id : int
        Current active tenant organization ID.
    user_id : Optional[str]
        Current authenticated user ID.
    session_state : Optional[Dict[str, Any]]
        Active session conversation context (last_executed_task, active_year, etc.).

    Returns
    -------
    Optional[FastRouteResult]
        Route result if matched, else None to fall through to Gemini LLM.
    """
    clean_q = query.strip()
    if CONCEPT_GUARD.search(clean_q):
        return None

    uid = user_id or settings.accutax_user_id
    today_date = dates.today()

    period_str = _extract_period_phrase(clean_q)
    anchor_date = today_date
    if session_state and session_state.get("active_year"):
        try:
            yr = int(session_state["active_year"])
            anchor_date = today_date.replace(year=yr)
        except Exception:
            pass

    window = dates.resolve(period_str, anchor=anchor_date)

    # 1. Check Standard Fast Router Regex Rules
    for pattern, rule_name, endpoint, intent in FAST_ROUTER_RULES:
        if pattern.search(clean_q):
            norm_sel = _build_params_for_endpoint(endpoint, organization_id, uid, window, clean_q, today_date)
            logger.info("FastRouter HIT: rule='%s' endpoint='%s'", rule_name, endpoint)
            METRICS.fast_router_hits.inc()

            return FastRouteResult(
                endpoint=norm_sel["endpoint"],
                method=norm_sel.get("method", "GET"),
                path_params=norm_sel.get("path_params", {}),
                query_params=norm_sel.get("query_params", {}),
                intent=intent,
                rule_name=rule_name,
            )

    # 2. Contextual Multi-Turn Follow-Up Check (Phase D)
    if session_state and session_state.get("last_executed_task"):
        last_task = str(session_state["last_executed_task"]).strip()
        canonical_ep = last_task if last_task.startswith("/") else TASK_TO_ENDPOINT.get(last_task)

        if canonical_ep:
            fu_match = FOLLOW_UP_PERIOD_REGEX.match(clean_q)
            if fu_match:
                rel_phrase = fu_match.group(1).strip()
                rel_window = dates.resolve(rel_phrase, anchor=anchor_date)
                norm_sel = _build_params_for_endpoint(canonical_ep, organization_id, uid, rel_window, clean_q, today_date)

                rule_name = f"contextual_follow_up_{rel_phrase.replace(' ', '_')}"
                logger.info("FastRouter CONTEXT HIT: inherited endpoint='%s' for follow-up='%s'", canonical_ep, clean_q)
                METRICS.fast_router_hits.inc()

                return FastRouteResult(
                    endpoint=norm_sel["endpoint"],
                    method=norm_sel.get("method", "GET"),
                    path_params=norm_sel.get("path_params", {}),
                    query_params=norm_sel.get("query_params", {}),
                    intent=3 if "report" in canonical_ep else 4,
                    rule_name=rule_name,
                    reason="contextual_follow_up_inherited_task",
                )

    return None

