"""
endpoint_selector.py — Gemini-driven API endpoint selection.

Phase 1 optimization:
- Restructured prompt for prefix caching ({catalog} and static rules at top, dynamic date/org/user at bottom).
- Removed redundant question interpolation from system prompt (passed cleanly as user content).
- Max tokens set to 200 (was 400).
- Exception handler distinguishes transient router failures from no-match and triggers keyword fallback.
"""
from __future__ import annotations

import datetime
import logging
from typing import Callable, Dict, Optional, Tuple

from gemini_brain.config.api_catalog import API_CATALOG
from gemini_brain.config.settings import settings
from gemini_brain.endpoints.keyword_fallback import keyword_endpoint_fallback
from gemini_brain.endpoints.param_normalizer import normalize_endpoint_params
from gemini_brain.observability.metrics import METRICS

logger = logging.getLogger("gemini_brain.endpoints.endpoint_selector")

# ── Prefix-Cached System Prompt Template (Static blocks at top) ───────────────
API_SELECTOR_SYSTEM_PROMPT: str = """You are an API endpoint selector for Accutax, a cloud-based accounting system.
Select the BEST REST API endpoint for the user question and build its query parameters.
Return ONLY valid JSON — no markdown, no explanation.

API CATALOG:
{catalog}

QUICK REFERENCE — use these exact endpoints for these query types:
  total sales / total revenue / total income / how much income → /income/total
  total expenses / total spending / total bills → /expense/total
  cash forecast / forecast cash flow / next X months cash / projected cash → /report/cash-forecast
  outstanding receivables / total owed to us / customer balances → /report/customer-balance-summary
  overdue invoices / aging report / who owes us → /report/ar-aging-summary
  P&L / profit and loss / net profit → /report/profit-loss
  balance sheet / assets liabilities → /report/balance-sheet
  top customers / sales by customer → /report/sales-by-customer
  expense by category / spending breakdown → /report/expense-by-category
  cash balance / bank balance → /bank/manual/accounts

RULES:
- Always include organization_id unless the endpoint says not to (e.g. /item/list, /currency/supported)
- For /income/list and /expense/list: use userId (camelCase), NOT organization_id
- For /accounting/journal-entries: use userId and organizationId (both camelCase)
- For /item/list: user_id MUST be a string like "18"
- Date shortcuts: "this month"={month_start} to {today}, "this year"={year_start} to {today}, "this quarter"={quarter_start} to {today}
- For /report/ar-aging-summary or /report/ap-aging-summary: use as_of_date={today}
- For /report/customer-balance-summary: no date range needed
- CRITICAL for /income/total and /expense/total: params MUST be user_id="{user_id}" (snake_case string), filter_year="2026" (4-digit year as string), filter_type="YEARLY". Do NOT use start_date, end_date, or userId for these endpoints.
- If no endpoint fits, return {{"endpoint": null, "reason": "no_api_match"}}

DYNAMIC CONTEXT:
TODAY: {today}
ORG_ID: {org_id}
USER_ID: {user_id}
MONTH_START: {month_start}
YEAR_START: {year_start}
QUARTER_START: {quarter_start}

Return JSON:
{{"endpoint": "/path/to/endpoint", "method": "GET", "path_params": {{}}, "query_params": {{"key": "value"}}, "reason": "..."}}"""


from gemini_brain.router.llm_router import select_endpoint_structured


def select_endpoint(
    query: str,
    org_id: int,
    call_gemini: Callable[[str, str, int], Tuple[str, int, int]],
    parse_json: Optional[Callable[[str, Dict], Dict]] = None,
    user_id: str = "",
    session_state: Optional[Dict[str, Any]] = None,
    feedback: Optional[str] = None,
) -> Tuple[Optional[Dict], int, int]:
    """Select the best REST API endpoint for query using Gemini Structured Function Calling.

    Parameters
    ----------
    query : str
        User question.
    org_id : int
        Organization ID.
    call_gemini : callable
        ``(system, user_text, max_tokens) -> (text, input_tokens, output_tokens)``
    parse_json : callable, optional
        Legacy parser (retained for backward compatibility).
    user_id : str
        Default user ID string.
    session_state : Optional[Dict[str, Any]]
        Active session conversation context.
    feedback : Optional[str]
        Diagnostic feedback from a failed previous attempt for self-correction.

    Returns
    -------
    Tuple[Optional[Dict], int, int]
        (selection_dict or None, input_tokens, output_tokens)
    """
    uid = user_id or settings.accutax_user_id
    today = datetime.date.today()

    try:
        sel, ri, ro = select_endpoint_structured(
            query=query,
            org_id=org_id,
            call_gemini=call_gemini,
            user_id=uid,
            session_state=session_state,
            feedback=feedback,
        )

        if not sel or not sel.get("endpoint"):
            # Keyword fallback for edge cases
            kw_sel = keyword_endpoint_fallback(query, org_id, today, user_id=uid)
            if kw_sel:
                sel = normalize_endpoint_params(kw_sel, org_id, today, user_id=uid)
                logger.info("GeminiBrain keyword fallback selected endpoint: %s", sel.get("endpoint"))
                return sel, ri, ro
            return None, ri, ro

        sel = normalize_endpoint_params(sel, org_id, today, user_id=uid)
        logger.info("GeminiBrain structured router selected endpoint: %s (tool=%s)", sel.get("endpoint"), sel.get("tool_name"))
        return sel, ri, ro

    except Exception as e:
        logger.warning(
            "structured endpoint selection transient failure: %s", e, extra={"query": query}
        )
        METRICS.router_transient_failures.inc()
        sel = keyword_endpoint_fallback(query, org_id, today, user_id=uid)
        if sel:
            sel = normalize_endpoint_params(sel, org_id, today, user_id=uid)
            logger.info("GeminiBrain emergency fallback selected endpoint: %s", sel["endpoint"])
            return sel, 0, 0
        return None, 0, 0

