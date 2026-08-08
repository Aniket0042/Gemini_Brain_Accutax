"""
endpoint_selector.py — Gemini-driven API endpoint selection.

Extracted from gemini_brain_adapter.py lines 71-110 (_API_SELECTOR_SYSTEM)
and lines 190-225 (_select_endpoint).
Uses verbatim prompt template, compact API_CATALOG, keyword fallback, and param normalization.
"""
from __future__ import annotations

import datetime
import logging
from typing import Callable, Dict, Optional, Tuple

from gemini_brain.config.api_catalog import API_CATALOG
from gemini_brain.config.settings import settings
from gemini_brain.endpoints.keyword_fallback import keyword_endpoint_fallback
from gemini_brain.endpoints.param_normalizer import normalize_endpoint_params

logger = logging.getLogger("gemini_brain.endpoints.endpoint_selector")

# ── Verbatim System Prompt ───────────────────────────────────────────────────
API_SELECTOR_SYSTEM_PROMPT: str = """You are an API endpoint selector for Accutax, a cloud-based accounting system.
Select the BEST REST API endpoint for the user question and build its query parameters.
Return ONLY valid JSON — no markdown, no explanation.

TODAY: {today}
ORG_ID: {org_id}
USER_ID: {user_id}
MONTH_START: {month_start}
YEAR_START: {year_start}
QUARTER_START: {quarter_start}

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

USER QUESTION: {question}

Return JSON:
{{"endpoint": "/path/to/endpoint", "method": "GET", "path_params": {{}}, "query_params": {{"key": "value"}}, "reason": "..."}}"""


def select_endpoint(
    query: str,
    org_id: int,
    call_gemini: Callable[[str, str, int], Tuple[str, int, int]],
    parse_json: Callable[[str, Dict], Dict],
    user_id: str = "",
) -> Tuple[Optional[Dict], int, int]:
    """Select the best REST API endpoint for query using Gemini 2.5 Flash.

    Parameters
    ----------
    query : str
        User question.
    org_id : int
        Organization ID.
    call_gemini : callable
        ``(system, user_text, max_tokens) -> (text, input_tokens, output_tokens)``
    parse_json : callable
        ``(text, default_dict) -> dict``
    user_id : str
        Default user ID string.

    Returns
    -------
    Tuple[Optional[Dict], int, int]
        (selection_dict or None, input_tokens, output_tokens)
    """
    uid = user_id or settings.accutax_user_id
    today = datetime.date.today()
    m_start = today.replace(day=1)
    y_start = today.replace(month=1, day=1)
    q_month = ((today.month - 1) // 3) * 3 + 1
    q_start = today.replace(month=q_month, day=1)

    prompt = API_SELECTOR_SYSTEM_PROMPT.format(
        today=today.isoformat(),
        org_id=org_id,
        user_id=uid,
        month_start=m_start.isoformat(),
        year_start=y_start.isoformat(),
        quarter_start=q_start.isoformat(),
        catalog=API_CATALOG,
        question=query,
    )

    try:
        text, ri, ro = call_gemini(prompt, query, max_tokens=400)
        sel = parse_json(text, {"endpoint": None})
        if not sel.get("endpoint"):
            # Keyword fallback for endpoints Gemini frequently misses
            sel = keyword_endpoint_fallback(query, org_id, today, user_id=uid)
            if not sel:
                return None, ri, ro
        sel = normalize_endpoint_params(sel, org_id, today, user_id=uid)
        logger.info("GeminiBrain selected endpoint: %s", sel["endpoint"])
        return sel, ri, ro
    except Exception as e:
        logger.warning("Endpoint selection failed: %s", e)
        return None, 0, 0
