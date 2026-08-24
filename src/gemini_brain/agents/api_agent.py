"""
api_agent.py — REST API routing layer for the 2-tier intelligence system.

Tier responsibilities:
  1. classify_route()  — Fast Haiku classifier: API | SIMPLE | COMPLEX
     API    → question can be answered by fetching records from the Accutax backend REST API
     SIMPLE → needs DB analytics but is single-period / single-table (handled by Haiku coordinator)
     COMPLEX→ multi-period, trend analysis, reports (handled by Sonnet coordinator)

  2. run_api_query()  — If routed to API:
     Step A: Endpoint selector (Haiku) — picks the right endpoint + builds params JSON
     Step B: HTTP call  — calls the Accutax backend
     Step C: Response formatter (Haiku) — converts raw JSON → natural language answer

Falls back to AGENT automatically if:
  - API call returns non-2xx
  - Backend is unreachable
  - Selected endpoint requires path params the LLM cannot infer
"""

from __future__ import annotations

import json
import logging
import os
import time
import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException, Timeout

from gemini_brain.agents.bedrock_client import converse, MODEL_ID_FAST, get_token_usage

logger = logging.getLogger("agents.api_agent")

# ─────────────────────────────────────────────────────────────────────────────
# Backend config (reads from env; accounting_tools shares the same vars)
# ─────────────────────────────────────────────────────────────────────────────
_BASE_URL    = os.getenv("ACCUTAX_BASE_URL",   "http://13.127.157.108:8081")
_AUTH_TOKEN  = os.getenv("ACCUTAX_AUTH_TOKEN", "")
_USER_ID     = os.getenv("ACCUTAX_USER_ID", "1")  # Keep as string — some endpoints require string userId
_HTTP_TIMEOUT = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# 3-WAY ROUTE CLASSIFIER PROMPT
# ─────────────────────────────────────────────────────────────────────────────
_ROUTE_CLASSIFIER_PROMPT = """You are a query router for a financial AI system. Classify each question into exactly one tier:

API — The backend has a REST endpoint that directly answers this question. Use API for ALL of these:
  LISTING / FETCHING records: invoices, expenses, customers, vendors, contacts, items, bank accounts, chart of accounts, journal entries, audit logs, projects, branches, cost centers, currencies, warehouses, tax rates
  FINANCIAL REPORTS the backend generates: P&L, balance sheet, AR aging, AP aging, cash flow, cash forecast, expense by category, sales by customer, sales by item, purchases by vendor, VAT report, tax liability, inventory valuation, COGS, customer balance summary, statement of account, top entries, invoice details, bills details
  TOTALS & METRICS: total revenue, total expenses, income totals, expense totals, outstanding AR, overdue amount
  DASHBOARD: financial dashboard, key metrics, financial summary
  FORECASTS: cash forecast, payment forecast, projected collections
  TAX: VAT report, VAT summary, tax liability, tax rates, VAT config
  INVENTORY: low stock, inventory quantities, inventory movements, inventory valuation
Examples → API:
- "show my last 20 invoices", "list all customers", "show AR aging report", "show P&L"
- "show expense breakdown by category", "top 5 customers by revenue"
- "what is my cash balance", "total outstanding AR", "how many overdue invoices"
- "show cash flow statement", "show cash forecast", "show balance sheet"
- "show VAT report", "tax liability this quarter", "show inventory valuation"
- "who are my top defaulters", "show customer balance summary"
- "show AP aging", "supplier outstanding balances"

COMPLEX — Use ONLY for multi-period COMPARISONS or TRENDS spanning multiple years or distinct time periods that would require calling the API multiple times and synthesizing results.
Examples → COMPLEX:
- "P&L 2024 vs 2025", "compare revenue Q1 2024 to Q1 2025"
- "year-over-year revenue growth from 2021 to 2025"
- "3-year expense trend", "monthly trend from 2022 to 2026"
- "which year was most profitable across all years"

SIMPLE — Use only for very custom DB analytics with NO corresponding API report endpoint.
Examples → SIMPLE: extremely niche aggregations not covered by any report.

RULE: When in doubt, choose API. The backend has reports for almost everything.
Respond with ONLY one word: API, SIMPLE, or COMPLEX"""


# ─────────────────────────────────────────────────────────────────────────────
# COMPACT API ENDPOINT CATALOG (injected into endpoint-selector prompt)
# ─────────────────────────────────────────────────────────────────────────────
_API_CATALOG = """## AVAILABLE REST API ENDPOINTS

### INVOICES / INCOME
GET /income/list
  query: userId* (string — REQUIRED, camelCase), page=1, pageSize=20, status (PAID|PENDING|PARTIALLY_PAID|CANCELLED), payment, search (text), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), sortBy, sortOrder
  → Paginated list of invoices/income records. NOTE: uses userId (camelCase), NOT user_id or organization_id

GET /income/find
  query: organization_id*, search (invoice number or name)
  → Search for specific income record

GET /income/total
  query: user_id* (string — REQUIRED, snake_case, must be a string like "18"), filter_year* (string — REQUIRED, e.g. "2026"), filter_type* (string — REQUIRED, MUST be one of: YEARLY, QUARTERLY, MONTHLY), organization_id
  → Income totals: returns total_income and total_tax for the specified period
  USE FOR: "total sales", "total revenue", "total income", "how much income", "total invoiced amount", "annual revenue"
  NOTE: Do NOT use start_date/end_date — use filter_year + filter_type instead

GET /income/customer-payment/list
  query: organization_id*, userId (camelCase), page=1, pageSize=20
  → Customer payment records

### EXPENSES / BILLS
GET /expense/list
  query: userId* (string — REQUIRED, camelCase), page=1, pageSize=20, status (PAID|PENDING|PARTIALLY_PAID|CANCELLED), payment, sortBy, sortOrder
  → Paginated list of expense bills. NOTE: uses userId (camelCase), NOT user_id or organization_id

GET /expense/find
  query: organization_id*, search
  → Search for specific expense

GET /expense/total
  query: organization_id*, start_date, end_date, userId (camelCase)
  → Expense totals and summary stats

GET /expense/supplier-payment/list
  query: organization_id*, userId (camelCase), page=1, pageSize=20
  → Supplier/vendor payment records

### BANKING
GET /bank/manual/accounts
  query: organization_id* (required), user_id
  → List of bank accounts with current balances

GET /bank/transactions/uncategorized
  query: organization_id* (required)
  → Uncategorized bank transactions

GET /bank/rules
  query: organization_id* (required)
  → Bank categorization rules

### CONTACTS
GET /contact/list
  query: organization_id* (required), user_id, contact_type_id (4=customer, 1=vendor, 2=vendor, 3=vendor), name, page=1, pageSize=20
  → List of contacts (customers or vendors)

GET /contact/find
  query: organization_id*, search (name or email)
  → Search for a specific contact

### CHART OF ACCOUNTS
GET /chart-of-accounts
  query: organization_id* (required)
  → Full chart of accounts

GET /chart-of-accounts/list
  query: organizationId* (required), userId
  → Chart of accounts with type breakdown

### DASHBOARD
GET /dashboard/web/v3
  query: user_id* (required), organization_id, year (YYYY e.g. "2026"), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Financial dashboard: revenue, expenses, outstanding, key metrics, monthly trend

### ACCOUNTING
GET /accounting/journal-entries
  query: userId* (string — camelCase, REQUIRED), organizationId* (camelCase, REQUIRED), search, page=1, pageSize=20
  → Journal entries list. NOTE: uses userId and organizationId (both camelCase)

GET /accounting/general-ledger
  query: organization_id*, account_code, start_date, end_date
  → General ledger entries

### AUDIT
GET /audit-logs
  query: organization_id*, page=1, limit=50, search, user_id, start_date, end_date
  → Audit log entries (who changed what)

GET /audit-trails
  query: organization_id*, page=1, limit=50
  → Audit trails

### ORGANIZATIONAL
GET /branches
  query: organization_id* (required)
  → Branches list

GET /cost-centers
  query: organization_id* (required)
  → Cost centers

GET /projects/list
  query: organization_id* (required)
  → Projects list

### CURRENCY
GET /currency/supported
  (no params needed)
  → List of supported currencies

GET /currency/exchange-rates
  (no params needed)
  → Current exchange rates

GET /currency/convert
  query: amount (number), targetCurrency (e.g. "USD")
  → Convert amount to target currency

### ITEMS / PRODUCTS
GET /item/list
  query: user_id* (string — REQUIRED, snake_case, must be a quoted string value e.g. "18" not integer 18), page=1, pageSize=20
  NOTE: user_id is required and MUST be a string. Do NOT include organization_id for this endpoint.
  → Items/products list

### FINANCIAL REPORTS
GET /report/profit-loss
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Profit & Loss / income statement for the period
  USE FOR: "P&L", "profit and loss", "income statement", "net profit", "gross profit"

GET /report/balance-sheet
  query: organization_id* (required), start_date, end_date
  → Balance sheet (assets, liabilities, equity)
  USE FOR: "balance sheet", "assets", "liabilities", "equity"

GET /report/cash-flow
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Cash flow statement (direct method)
  USE FOR: "cash flow", "cash movement", "cash in/out"

GET /report/cash-flow-indirect
  query: organization_id* (required), start_date, end_date
  → Cash flow statement (indirect method)

GET /report/cash-forecast
  query: organization_id* (required), start_date, end_date
  → Cash forecast / projected cash position — weekly cash flow projection with overdue, upcoming inflows and outflows
  USE FOR: "cash forecast", "projected cash", "payment forecast", "expected collections", "cash projection", "forecast cash flow", "cash flow forecast", "cash flow next months", "predict cash", "upcoming cash", "future cash position"

GET /report/profit-loss-with-accounts
  query: organization_id* (required), start_date, end_date
  → P&L with full account-level breakdown

### AR / AP AGING REPORTS
GET /report/ar-aging-summary
  query: organization_id* (required), as_of_date (YYYY-MM-DD, defaults to today)
  → Accounts receivable aging summary (current, 30d, 60d, 90d, 120d+ buckets)
  USE FOR: "AR aging", "aging report", "receivables aging", "overdue invoices", "outstanding AR", "defaulters", "who owes us"

GET /report/customer-balance-summary
  query: organization_id* (required)
  → Outstanding balance per customer (who owes what) — shows invoiced_amount, invoice_received, closing_balance per customer
  USE FOR: "customer balances", "top defaulters", "who owes us the most", "customer outstanding", "total outstanding receivables", "total receivables", "outstanding receivables", "total amount owed", "who owes us"

GET /report/statement-of-account
  query: organization_id* (required), contact_id, start_date, end_date
  → Customer statement of account (full history)

GET /report/ap-aging-summary
  query: organization_id* (required), as_of_date (YYYY-MM-DD)
  → Accounts payable aging summary (what we owe vendors)
  USE FOR: "AP aging", "payables aging", "vendor outstanding", "bills overdue"

GET /report/ap-aging-details
  query: organization_id* (required), as_of_date
  → Detailed AP aging per bill

GET /report/aged-payables
  query: organization_id* (required)
  → Aged payables summary

GET /report/supplier-statement-of-account
  query: organization_id* (required), contact_id, start_date, end_date
  → Supplier statement of account

### SALES REPORTS
GET /report/sales-by-customer
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Sales ranked by customer — revenue per customer
  USE FOR: "top customers", "sales by customer", "best customers", "customer revenue ranking"

GET /report/sales-by-items
  query: organization_id* (required), start_date, end_date
  → Sales breakdown by item/product
  USE FOR: "top selling items", "sales by product", "best selling products"

GET /report/sales-by-branch
  query: organization_id* (required), start_date, end_date
  → Revenue by branch

GET /report/sales-by-project
  query: organization_id* (required), start_date, end_date
  → Revenue by project

GET /report/invoice-details
  query: organization_id* (required), start_date, end_date
  → Detailed invoice listing report

### EXPENSE REPORTS
GET /report/expense-by-category
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Expenses grouped by category
  USE FOR: "expense breakdown", "expense by category", "spending by category", "what are we spending on"

GET /report/expenses-by-contact
  query: organization_id* (required), start_date, end_date
  → Expenses grouped by vendor/supplier

GET /report/expenses-by-branch
  query: organization_id* (required), start_date, end_date
  → Expenses by branch

GET /report/purchases-by-vendor
  query: organization_id* (required), start_date, end_date
  → Purchases ranked by vendor
  USE FOR: "top vendors", "purchases by supplier", "spending by vendor"

GET /report/purchases-by-item
  query: organization_id* (required), start_date, end_date
  → Purchases by item

GET /report/bills-by-contact
  query: organization_id* (required), start_date, end_date
  → Bills grouped by contact

GET /report/bills-details
  query: organization_id* (required), start_date, end_date
  → Detailed bills report

### TAX / VAT REPORTS
GET /report/vat-report
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Full VAT return report (output tax, input tax, net payable)
  USE FOR: "VAT report", "VAT return", "VAT filing"

GET /report/vat-summary
  query: organization_id* (required), start_date, end_date
  → VAT summary (totals by rate)

GET /report/vat-input-output
  query: organization_id* (required), start_date, end_date
  → VAT input vs output breakdown

GET /report/tax-liability
  query: organization_id* (required), start_date, end_date
  → Tax liability report
  USE FOR: "tax liability", "taxes owed", "tax payable"

GET /tax-rate/list
  query: organization_id* (required)
  → List all configured tax rates

GET /vat-config/main
  query: organization_id* (required)
  → Active VAT configuration and rates

### INVENTORY REPORTS
GET /inventory/low-stock
  query: organization_id* (required)
  → Items below reorder level (low stock alerts)
  USE FOR: "low stock", "items running out", "reorder needed"

GET /inventory/quantities
  query: organization_id* (required)
  → Current stock quantities for all items
  USE FOR: "stock levels", "inventory quantities", "how many in stock"

GET /inventory/reports/valuation
  query: organization_id* (required)
  → Inventory valuation (stock value at cost)
  USE FOR: "inventory value", "stock worth", "inventory valuation"

GET /inventory/reports/cogs
  query: organization_id* (required), start_date, end_date
  → Cost of goods sold
  USE FOR: "COGS", "cost of goods sold", "cost of sales"

GET /inventory/reports/movement
  query: organization_id* (required), start_date, end_date
  → Inventory movement (in/out)
  USE FOR: "stock movement", "inventory in/out", "goods received/sold"

GET /inventory/movements
  query: organization_id* (required)
  → Raw inventory movement records

### P&L BY DIMENSION
GET /report/profit-loss-by-branch
  query: organization_id* (required), start_date, end_date
  → P&L broken down by branch

GET /report/profit-loss-by-project
  query: organization_id* (required), start_date, end_date
  → P&L broken down by project

GET /report/profit-loss-by-cost-center
  query: organization_id* (required), start_date, end_date
  → P&L broken down by cost center

GET /report/consolidated-pnl
  query: organization_id* (required), start_date, end_date
  → Consolidated P&L across entities

GET /report/consolidated-balance-sheet
  query: organization_id* (required)
  → Consolidated balance sheet

GET /report/consolidated-cash
  query: organization_id* (required), start_date, end_date
  → Consolidated cash flow

### TOP ENTRIES
GET /report/top-entries
  query: organization_id* (required), start_date, end_date, type (income|expense|contact)
  → Top income / expense entries for a period
  USE FOR: "top transactions", "largest entries", "biggest invoices"

### WAREHOUSES / TRANSFERS
GET /warehouse/list
  query: organization_id* (required)
  → Warehouse list

GET /transfers
  query: organization_id* (required), page=1, limit=20
  → Inventory transfers between warehouses
"""


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT SELECTOR PROMPT
# ─────────────────────────────────────────────────────────────────────────────
_ENDPOINT_SELECTOR_TEMPLATE = """You are an API endpoint selector. Given a user question and a catalog of REST API endpoints, select the BEST endpoint and build the query parameters.

TODAY: {today}
ORG ID: {org_id} (always include as organization_id in params)
USER ID: {user_id} (IMPORTANT: some endpoints use userId camelCase, others use user_id snake_case — follow the catalog exactly)

{catalog}

## OUTPUT FORMAT (respond with ONLY valid JSON — no explanation, no markdown)
{{
  "endpoint": "/path/to/endpoint",
  "method": "GET",
  "path_params": {{}},
  "query_params": {{
    "organization_id": {org_id},
    "page": 1,
    "pageSize": 20
  }},
  "description": "one sentence: what this call fetches"
}}

## RULES
- Always include organization_id in query_params unless the endpoint has no such param (e.g. /currency/supported, /item/list)
- For /dashboard/web/v3: always include user_id={user_id} and the appropriate year/date range from the question
- For /contact/list: set contact_type_id=4 for customer questions, contact_type_id=1 for vendor questions
- For /income/list or /expense/list: use "userId": "{user_id}" (camelCase string) — do NOT include organization_id for these endpoints
- For /accounting/journal-entries: use "userId": "{user_id}" and "organizationId": {org_id} (both camelCase)
- For /income/list or /expense/list: set reasonable pageSize (20 for "recent", 50 for "all")
- For date-filtered questions: convert relative dates (e.g. "this month" = {month_start} to {today}, "this year" = {year_start} to {today}, "this quarter" = start of current quarter to {today})
- For /chart-of-accounts: use organization_id only, no pagination needed
- For all /report/* endpoints: always include organization_id. Add start_date/end_date when the question mentions a time period. Default: start_date={year_start}, end_date={today}
- For /report/ar-aging-summary or /report/ap-aging-summary: use as_of_date={today} (not start_date/end_date)
- For /report/customer-balance-summary: use organization_id only (no date range needed)
- For /inventory/* endpoints: always include organization_id
- For /tax-rate/list or /vat-config/main: always include organization_id
- ONLY select endpoints from the catalog above. If no endpoint fits perfectly, choose the closest one.

USER QUESTION: {question}
"""


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSE FORMATTER PROMPT
# ─────────────────────────────────────────────────────────────────────────────
_FORMATTER_TEMPLATE = """You are a financial data analyst presenting data from a live accounting system.

USER QUESTION: {question}
API ENDPOINT CALLED: {endpoint}
DATA RETURNED:
{data_preview}

## INSTRUCTIONS
- Give a clear, direct answer to the user's question using the data above
- Format currency values as AED X,XXX.XX (this is a UAE company)
- Use bullet points or a numbered list for multiple records
- For invoice/expense lists: show key fields (number, date, contact, amount, status)
- For contacts: show name, type, email/phone if available
- For accounts/banking: show account names and balances clearly
- For dashboard: highlight the key financial metrics clearly
- If the data is empty or has 0 records: say "No records found" and explain what was searched
- Be concise but complete — do NOT dump raw JSON or IDs
- Do NOT mention the API endpoint or technical details
- Start your answer directly (no "Based on the data..." preamble)
"""


# ─────────────────────────────────────────────────────────────────────────────
# classify_route — 3-way router: API | SIMPLE | COMPLEX
# ─────────────────────────────────────────────────────────────────────────────
def classify_route(question: str) -> str:
    """
    Fast Haiku classification of query routing tier.
    Returns one of: 'API', 'SIMPLE', 'COMPLEX'
    """
    try:
        result = converse(
            system_prompt=_ROUTE_CLASSIFIER_PROMPT,
            messages=[{"role": "user", "content": [{"text": question}]}],
            temperature=0.0,
            max_tokens=5,
            model_id=MODEL_ID_FAST,
        )
        tier = result.strip().upper()
        if tier not in ("API", "SIMPLE", "COMPLEX"):
            logger.warning("Route classifier returned unexpected value: %r — defaulting SIMPLE", tier)
            return "SIMPLE"
        logger.info("Route classified: %s for question: %.80s", tier, question)
        return tier
    except Exception as e:
        logger.warning("Route classifier failed (%s) — defaulting SIMPLE", e)
        return "SIMPLE"


# ─────────────────────────────────────────────────────────────────────────────
# _build_selector_prompt
# ─────────────────────────────────────────────────────────────────────────────
def _build_selector_prompt(question: str, org_id: int, user_id: int) -> str:
    today = datetime.date.today()
    month_start = today.replace(day=1).isoformat()
    year_start  = today.replace(month=1, day=1).isoformat()
    return _ENDPOINT_SELECTOR_TEMPLATE.format(
        today=today.isoformat(),
        org_id=org_id,
        user_id=user_id,
        catalog=_API_CATALOG,
        month_start=month_start,
        year_start=year_start,
        question=question,
    )


# ─────────────────────────────────────────────────────────────────────────────
# _select_endpoint — use Haiku to pick endpoint + params
# ─────────────────────────────────────────────────────────────────────────────
def _select_endpoint(question: str, org_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Call Haiku to select the best API endpoint and build params. Returns parsed dict or None."""
    prompt = _build_selector_prompt(question, org_id, user_id)
    try:
        raw = converse(
            system_prompt=prompt,
            messages=[{"role": "user", "content": [{"text": question}]}],
            temperature=0.0,
            max_tokens=400,
            model_id=MODEL_ID_FAST,
        )
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        parsed = json.loads(text)
        logger.info("Endpoint selected: %s %s", parsed.get("method"), parsed.get("endpoint"))
        return parsed
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Endpoint selector parse error: %s — raw: %.200s", e, raw if 'raw' in dir() else "")
        return None
    except Exception as e:
        logger.warning("Endpoint selector failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# _call_api — execute the HTTP call against the Accutax backend
# ─────────────────────────────────────────────────────────────────────────────
def _call_api(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
) -> Tuple[bool, Any]:
    """
    Execute a GET call against the Accutax backend.
    Returns (success: bool, data: Any) where data is the parsed JSON or error string.
    """
    # Build URL (substitute path params if any)
    url_path = endpoint
    for key, val in path_params.items():
        url_path = url_path.replace(f":{key}", str(val))
        url_path = url_path.replace(f"{{{key}}}", str(val))

    url = f"{_BASE_URL.rstrip('/')}/{url_path.lstrip('/')}"

    # Build headers
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if _AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {_AUTH_TOKEN}"

    # Remove None values from query params
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    # Some endpoints require user_id / userId as a string, not an integer
    for key in ("user_id", "userId"):
        if key in clean_params and not isinstance(clean_params[key], str):
            clean_params[key] = str(clean_params[key])

    logger.info("API CALL: GET %s params=%s", url, clean_params)

    try:
        resp = requests.get(
            url,
            headers=headers,
            params=clean_params,
            timeout=_HTTP_TIMEOUT,
        )
        logger.info("API RESPONSE: status=%d url=%s", resp.status_code, url)

        if resp.status_code == 401:
            return False, "Authentication required — ACCUTAX_AUTH_TOKEN not configured or expired."
        if resp.status_code == 404:
            return False, f"Endpoint not found: {endpoint}"
        if resp.status_code >= 400:
            return False, f"API error {resp.status_code}: {resp.text[:300]}"

        data = resp.json()
        return True, data

    except Timeout:
        logger.warning("API TIMEOUT: %s", url)
        return False, f"API request timed out after {_HTTP_TIMEOUT}s"
    except RequestException as e:
        logger.warning("API REQUEST ERROR: %s — %s", url, e)
        return False, f"API request failed: {str(e)}"
    except json.JSONDecodeError:
        return False, "API returned non-JSON response"


# ─────────────────────────────────────────────────────────────────────────────
# _extract_data — pull the payload out of various response envelope shapes
# ─────────────────────────────────────────────────────────────────────────────
def _extract_data(raw: Any) -> Any:
    """
    Accutax backend wraps responses in sendSuccessResponse envelope:
      { "success": true, "data": { ... } }  or  { "data": [...] }  or bare array/object
    """
    if isinstance(raw, dict):
        # Standard envelope
        if "data" in raw:
            return raw["data"]
        # Success flag with inner key
        if raw.get("success") and len(raw) == 2:
            return next(v for k, v in raw.items() if k != "success")
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# _format_api_response — use Haiku to format raw API data → natural language
# ─────────────────────────────────────────────────────────────────────────────
def _format_api_response(question: str, endpoint: str, raw_data: Any) -> str:
    """Format the API response JSON into a natural language answer."""
    # Compact the data: limit to first 30 records for lists, 4000 chars total
    data = _extract_data(raw_data)

    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        preview_data = {**data, "items": data["items"][:30]}
    elif isinstance(data, list):
        preview_data = data[:30]
    else:
        preview_data = data

    data_str = json.dumps(preview_data, default=str, ensure_ascii=False)
    if len(data_str) > 4000:
        data_str = data_str[:4000] + "\n... (truncated)"

    prompt = _FORMATTER_TEMPLATE.format(
        question=question,
        endpoint=endpoint,
        data_preview=data_str,
    )

    try:
        answer = converse(
            system_prompt=prompt,
            messages=[{"role": "user", "content": [{"text": "Please format the above data into a clear answer."}]}],
            temperature=0.0,
            max_tokens=1200,
            model_id=MODEL_ID_FAST,
        )
        return answer.strip()
    except Exception as e:
        logger.warning("Response formatter failed: %s", e)
        # Fallback: simple text summary
        if isinstance(data, list):
            return f"Retrieved {len(data)} records from the API."
        elif isinstance(data, dict):
            return f"API data: {json.dumps(data, default=str)[:500]}"
        return "Data retrieved from API but formatting failed."


# ─────────────────────────────────────────────────────────────────────────────
# run_api_query — main entry point for API-routed questions
# ─────────────────────────────────────────────────────────────────────────────
def run_api_query(
    question: str,
    org_id: int = 199,
    user_id: int = 1,
) -> Dict[str, Any]:
    """
    Handle a question via the Accutax REST API.

    Flow:
      1. Haiku selects endpoint + params
      2. HTTP GET to Accutax backend
      3. Haiku formats JSON response → natural language

    Returns a dict compatible with coordinator_agent.run() output shape:
      { query, answer, question_type, sql, results, agent_trace, token_usage, total_count, error }

    On failure (API unreachable, auth error, etc.): sets error field and falls back gracefully.
    """
    start = time.time()
    agent_trace: list = []

    # ── Step A: Select endpoint ──────────────────────────────────────────────
    selection = _select_endpoint(question, org_id, user_id)
    if not selection:
        return _fallback(question, "Could not determine which API endpoint to call.", start)

    endpoint    = selection.get("endpoint", "")
    path_params = selection.get("path_params", {}) or {}
    query_params = selection.get("query_params", {}) or {}
    description = selection.get("description", endpoint)

    agent_trace.append({
        "step": "endpoint_selection",
        "endpoint": endpoint,
        "params": query_params,
        "description": description,
    })

    if not endpoint:
        return _fallback(question, "Endpoint selector returned empty endpoint.", start)

    # ── Step B: Call the API ─────────────────────────────────────────────────
    success, raw_data = _call_api(endpoint, path_params, query_params)

    agent_trace.append({
        "step": "api_call",
        "endpoint": endpoint,
        "success": success,
        "error": None if success else str(raw_data)[:200],
    })

    if not success:
        error_msg = str(raw_data)
        logger.warning("API call failed for %s: %s", endpoint, error_msg)
        return _fallback(question, f"API call failed: {error_msg}", start)

    # ── Step C: Format response ───────────────────────────────────────────────
    answer = _format_api_response(question, endpoint, raw_data)

    elapsed = time.time() - start
    token_usage = get_token_usage()
    token_usage["elapsed_seconds"] = round(elapsed, 2)
    token_usage["route"] = "api"

    logger.info("API query completed in %.2fs via %s", elapsed, endpoint)

    return {
        "query":         question,
        "answer":        answer,
        "question_type": "api_fetch",
        "sql":           None,
        "results":       None,
        "agent_trace":   agent_trace,
        "token_usage":   token_usage,
        "total_count":   None,
        "error":         None,
        "route":         "api",
        "api_endpoint":  endpoint,
    }


# ─────────────────────────────────────────────────────────────────────────────
# _fallback — signals to the caller that API routing failed → use AGENT instead
# ─────────────────────────────────────────────────────────────────────────────
def _fallback(question: str, reason: str, start: float) -> Dict[str, Any]:
    """Return a special dict with route='agent_fallback' so the caller can re-route."""
    elapsed = time.time() - start
    logger.warning("API agent fallback: %s", reason)
    return {
        "query":         question,
        "answer":        None,
        "question_type": "unknown",
        "sql":           None,
        "results":       None,
        "agent_trace":   [{"step": "api_fallback", "reason": reason}],
        "token_usage":   {"elapsed_seconds": round(elapsed, 2)},
        "total_count":   None,
        "error":         reason,
        "route":         "agent_fallback",
    }
