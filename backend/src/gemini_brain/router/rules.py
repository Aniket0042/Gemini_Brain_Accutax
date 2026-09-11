"""
rules.py — Consolidated, declarative routing rules for Gemini Brain.

Single Source of Truth for:
1. Fast Router rules (`FAST_ROUTER_RULES` in `fast_router.py`)
2. SQL Fallback Fast Path (`_FAST_PATH` in `sql_fallback/fast_path.py`)
3. Keyword Fallback rules (`keyword_endpoint_fallback` in `keyword_fallback.py`)
4. LLM API Catalog Quick Reference hints (`api_catalog.py`)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import re
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

from gemini_brain.router.dates import Window, date_filters_from_request, forecast_months
from gemini_brain.utils.ranking import extract_direction_from_text
from gemini_brain.utils.ranking import extract_limit_from_text as _extract_limit_from_query
from gemini_brain.utils.ranking import extract_requested_count


def _safe_int(m: Any, group: int, default: int = 10) -> int:
    try:
        v = m.group(group)
        return int(v) if v else default
    except (IndexError, AttributeError, TypeError, ValueError):
        return default


def _query_text(m: Any) -> str:
    """SQL fast-path passes a regex match; empty-result verification passes the raw query string."""
    if m is None:
        return ""
    if isinstance(m, str):
        return m
    return str(getattr(m, "string", "") or "")


@dataclass(frozen=True)
class RoutingRule:
    name: str
    patterns: List[Pattern]
    endpoint: str
    sql_task: Optional[str]
    intent: int
    quick_reference_hint: Optional[str] = None
    keyword_triggers: List[str] = field(default_factory=list)


# Date-ranged Nest /report/* (and the one SQL income total) that take start/end.
PERIOD_REPORT_ENDPOINTS = frozenset({
    "rpt_income_total",
    "/report/profit-loss",
    "/report/profit-loss-with-accounts",
    "/report/profit-loss-by-project",
    "/report/profit-loss-by-cost-center",
    "/report/profit-loss-by-branch",
    "/report/cash-flow",
    "/report/cash-flow-indirect",
    "/report/sales-by-customer",
    "/report/sales-by-items",
    "/report/sales-by-project",
    "/report/sales-by-branch",
    "/report/expense-by-category",
    "/report/expenses-by-contact",
    "/report/bills-by-contact",
    "/report/purchases-by-vendor",
    "/report/purchases-by-item",
    "/report/vat-report",
    "/report/vat-summary",
    "/report/vat-input-output",
    "/report/tax-liability",
    "/report/estimate-conversion-rate",
    "/report/supplier-statement-of-account",
    "/report/export-return",
    "/report/invoice-details",
    "/report/aged-payables-detail",
})

# Ranked reports: pass limit + sort_order so "least/top N" survives into SQL fallback.
RANKED_REPORT_ENDPOINTS = frozenset({
    "/report/profit-loss-by-project",
    "/report/profit-loss-by-cost-center",
    "/report/profit-loss-by-branch",
    "/report/sales-by-project",
    "/report/sales-by-branch",
    "/report/sales-by-customer",
    "/report/sales-by-items",
    "/report/expenses-by-contact",
    "/report/bills-by-contact",
    "/report/purchases-by-vendor",
    "/report/purchases-by-item",
    "/report/invoice-details",
    "/report/aged-payables-detail",
    "/report/supplier-statement-of-account",
})


ROUTING_RULES: List[RoutingRule] = [
    RoutingRule(
        # Charts / "income report" need the P&L API (monthly + line items), not
        # the scalar income-total SQL report. Must sit above income_total.
        name="income_chart_or_report",
        patterns=[
            re.compile(
                r"\b((?:total\s+)?(?:sales|revenue|income).{0,48}"
                r"(?:charts?|graphs?|reports?|trend(?:line)?s?|visuali[sz]e)|"
                r"(?:charts?|graphs?|reports?|trend(?:line)?s?|visuali[sz]e).{0,48}"
                r"(?:sales|revenue|income))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/profit-loss",
        sql_task="profit_and_loss",
        intent=3,
        quick_reference_hint="income/revenue chart or report → /report/profit-loss",
        keyword_triggers=[],
    ),
    RoutingRule(
        name="profit_loss_by_project",
        patterns=[
            re.compile(
                r"\b((?:p\s*&\s*l|p&l|pnl|profit(?:\s+and\s+loss)?|revenue|profitability)\s+by\s+projects?|"
                r"projects?\s+(?:p\s*&\s*l|p&l|pnl|profit|revenue)|"
                r"(?:least|most|top|bottom)\s+profitable\s+projects?)\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/profit-loss-by-project",
        sql_task="project_profitability",
        intent=4,
        quick_reference_hint="P&L / revenue by project → /report/profit-loss-by-project",
        keyword_triggers=["p&l by project", "profit by project", "profitable projects"],
    ),
    RoutingRule(
        name="profit_loss_by_cost_center",
        patterns=[
            re.compile(
                r"\b((?:p\s*&\s*l|p&l|pnl|profit(?:\s+and\s+loss)?|revenue)\s+by\s+cost\s*cent(?:er|re)s?|"
                r"cost\s*cent(?:er|re)s?\s+(?:p\s*&\s*l|p&l|pnl|breakdown|revenue))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/profit-loss-by-cost-center",
        sql_task="cost_center_breakdown",
        intent=4,
        quick_reference_hint="P&L by cost centre → /report/profit-loss-by-cost-center",
        keyword_triggers=["p&l by cost center", "revenue by cost centre"],
    ),
    RoutingRule(
        name="profit_loss_by_branch",
        patterns=[
            re.compile(
                r"\b((?:p\s*&\s*l|p&l|pnl|profit(?:\s+and\s+loss)?|revenue)\s+by\s+branch|"
                r"branch(?:es)?\s+(?:p\s*&\s*l|p&l|pnl|profit|revenue))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/profit-loss-by-branch",
        sql_task="branch_summary",
        intent=4,
        quick_reference_hint="P&L by branch → /report/profit-loss-by-branch",
        keyword_triggers=["p&l by branch", "revenue by branch"],
    ),
    RoutingRule(
        # endpoint is a DB report (rpt_income_total), not the REST /income/total
        # endpoint. That REST endpoint was found to ignore organization_id
        # entirely -- it returned the identical total for every org tested,
        # including ones that don't exist -- because the REST backend and this
        # Postgres database are separate systems with no data overlap for these
        # tenants. sql_task is unchanged: it independently feeds the SQL
        # fallback tier's own direct-DB fast path (get_sql_fast_path_rules),
        # which was already correct and untouched by this.
        # Charts/reports of income are routed above to /report/profit-loss.
        name="income_total",
        patterns=[
            re.compile(r"\b(total (sales|revenue|income)|how much (income|revenue)|annual revenue|total invoiced amount)\b", re.IGNORECASE),
            re.compile(r"(?:total|annual)\s+(?:sales|revenue|income)|how\s+much\s+(?:sales|revenue|income)\s+did\s+we\s+make", re.IGNORECASE),
        ],
        endpoint="rpt_income_total",
        sql_task="get_invoice_total",
        intent=4,
        quick_reference_hint="total sales / total revenue / total income / how much income → rpt_income_total (direct DB read)",
        keyword_triggers=["total sales", "total revenue", "total income", "how much income", "how much revenue", "how much sales"],
    ),
    RoutingRule(
        name="expense_total",
        patterns=[
            re.compile(r"\b(total (expenses?|spending)|total bills|overall expenditures?)\b", re.IGNORECASE),
            re.compile(r"how\s+much\s+(?:expenses?|spending|bills)|total\s+(?:spending|expenditures?)", re.IGNORECASE),
        ],
        endpoint="/expense/total",
        sql_task="aggregate_metric",
        intent=4,
        quick_reference_hint="total expenses / total spending / total bills → /expense/total",
        keyword_triggers=["total expenses", "total expense", "total spending", "total bills", "how much expense", "how much spend"],
    ),
    RoutingRule(
        name="profit_loss",
        patterns=[
            re.compile(
                r"\b(p\s*&\s*l|p&l|pnl\b|profit\s*(?:and|&)\s*loss|"
                r"income statement|net earnings breakdown|net profit)\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/profit-loss",
        sql_task="profit_and_loss",
        intent=3,
        quick_reference_hint="P&L / profit and loss / net profit → /report/profit-loss",
        keyword_triggers=["p&l", "profit and loss", "income statement", "net profit", "gross profit"],
    ),
    RoutingRule(
        name="balance_sheet",
        patterns=[
            re.compile(r"\b(balance sheet|assets and liabilities|net worth and asset breakdown)\b", re.IGNORECASE),
        ],
        endpoint="/report/balance-sheet",
        sql_task="balance_sheet",
        intent=3,
        quick_reference_hint="balance sheet / assets liabilities → /report/balance-sheet",
        keyword_triggers=["balance sheet", "assets", "liabilities"],
    ),
    RoutingRule(
        name="cash_forecast",
        patterns=[
            re.compile(r"\b(cash forecast|projected cash|cash runway|expected cash flow|forecast cash flow)\b", re.IGNORECASE),
        ],
        endpoint="/report/cash-forecast",
        sql_task="payment_forecast",
        intent=5,
        quick_reference_hint="cash forecast / forecast cash flow / next X months cash / projected cash → /report/cash-forecast",
        keyword_triggers=["cash forecast", "forecast cash", "cash flow forecast", "cash projection", "projected cash", "cash runway"],
    ),
    RoutingRule(
        name="cash_flow",
        patterns=[
            re.compile(r"\b(cash flow statement|cash inflows and outflows)\b", re.IGNORECASE),
        ],
        endpoint="/report/cash-flow",
        sql_task="cash_flow_statement",
        intent=3,
        quick_reference_hint="cash flow statement / cash movement → /report/cash-flow",
        keyword_triggers=["cash flow", "cash movement", "cash in/out"],
    ),
    RoutingRule(
        name="ar_aging",
        patterns=[
            re.compile(r"\b(who owes us|overdue invoices?|aging report|aged receivables?|accounts? receivable aging)\b", re.IGNORECASE),
            re.compile(r"(?:ar|accounts?\s+receivable)\s+aging|aged\s+receivables?", re.IGNORECASE),
        ],
        endpoint="/report/ar-aging-summary",
        sql_task="ar_aging",
        intent=4,
        quick_reference_hint="overdue invoices / aging report / who owes us → /report/ar-aging-summary",
        keyword_triggers=["ar aging", "aging report", "receivables aging", "overdue invoices", "who owes us"],
    ),
    RoutingRule(
        name="ap_aging",
        patterns=[
            re.compile(r"\b(who do we owe|overdue bills?|aged payables?|accounts? payable aging|unpaid supplier bills)\b", re.IGNORECASE),
            re.compile(r"(?:ap|accounts?\s+payable)\s+aging|aged\s+payables?", re.IGNORECASE),
        ],
        endpoint="/report/ap-aging-summary",
        sql_task="ap_aging",
        intent=4,
        quick_reference_hint="AP aging / payables aging / vendor outstanding → /report/ap-aging-summary",
        keyword_triggers=["ap aging", "payables aging", "vendor outstanding", "bills overdue"],
    ),
    RoutingRule(
        name="customer_balance_summary",
        patterns=[
            re.compile(r"\b(customer balances?|outstanding (customer|receivables?)|clients? with.*unpaid balances?)\b", re.IGNORECASE),
        ],
        endpoint="/report/customer-balance-summary",
        sql_task="customer_overdue_summary",
        intent=4,
        quick_reference_hint="outstanding receivables / total owed to us / customer balances → /report/customer-balance-summary",
        keyword_triggers=["customer balance", "receivables", "who owes us", "outstanding ar", "customer outstanding"],
    ),
    RoutingRule(
        name="sales_by_customer",
        patterns=[
            re.compile(
                r"\b(top|bottom|least|lowest|worst)\s+customers?\b|"
                r"\bsales by customer\b|\bhighest grossing buyers?\b",
                re.IGNORECASE,
            ),
            re.compile(r"(?:top|bottom|least|lowest|worst)\s+(\d+)\s+customers?", re.IGNORECASE),
        ],
        endpoint="/report/sales-by-customer",
        sql_task="top_customers",
        intent=4,
        quick_reference_hint="top customers / sales by customer → /report/sales-by-customer",
        keyword_triggers=["top customers", "bottom customers", "sales by customer", "best customers", "worst customers"],
    ),
    RoutingRule(
        name="sales_by_project",
        patterns=[
            re.compile(r"\b(sales by project|invoices? per project|revenue per project)\b", re.IGNORECASE),
        ],
        endpoint="/report/sales-by-project",
        sql_task="project_profitability",
        intent=4,
        quick_reference_hint="sales by project → /report/sales-by-project",
        keyword_triggers=["sales by project"],
    ),
    RoutingRule(
        name="sales_by_branch",
        patterns=[
            re.compile(r"\b(sales by branch|revenue per branch)\b", re.IGNORECASE),
        ],
        endpoint="/report/sales-by-branch",
        sql_task="branch_summary",
        intent=4,
        quick_reference_hint="sales by branch → /report/sales-by-branch",
        keyword_triggers=["sales by branch"],
    ),
    RoutingRule(
        name="estimate_conversion",
        patterns=[
            re.compile(
                r"\b(estimate conversion|quote conversion|quote win rate|"
                r"how many (?:quotes?|estimates?) (?:did we )?(?:win|convert|accept))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/estimate-conversion-rate",
        sql_task=None,
        intent=4,
        quick_reference_hint="estimate/quote conversion → /report/estimate-conversion-rate",
        keyword_triggers=["estimate conversion", "quote conversion", "quote win rate"],
    ),
    RoutingRule(
        name="vat_input_output",
        patterns=[
            re.compile(
                r"\b(vat input(?:\s+and|\s+vs\.?|\s*/\s*)?\s*output|monthly vat breakdown|"
                r"vat (?:in|out)put)\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/vat-input-output",
        sql_task="vat_summary",
        intent=3,
        quick_reference_hint="VAT input vs output → /report/vat-input-output",
        keyword_triggers=["vat input", "vat output", "monthly vat"],
    ),
    RoutingRule(
        name="bills_by_contact",
        patterns=[
            re.compile(r"\b(bills by (?:vendor|supplier|contact)|purchases per contact)\b", re.IGNORECASE),
        ],
        endpoint="/report/bills-by-contact",
        sql_task="overdue_bills",
        intent=4,
        quick_reference_hint="bills by vendor → /report/bills-by-contact",
        keyword_triggers=["bills by vendor", "bills by contact"],
    ),
    RoutingRule(
        name="expenses_by_contact",
        patterns=[
            re.compile(r"\b(expenses? by (?:contact|vendor|supplier))\b", re.IGNORECASE),
        ],
        endpoint="/report/expenses-by-contact",
        sql_task="expense_by_category",
        intent=4,
        quick_reference_hint="expenses by contact → /report/expenses-by-contact",
        keyword_triggers=["expenses by contact", "expenses by vendor"],
    ),
    RoutingRule(
        name="supplier_statement",
        patterns=[
            re.compile(
                r"\b(supplier statement(?: of account)?|vendor statement of account|"
                r"what do we owe each supplier)\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/report/supplier-statement-of-account",
        sql_task="ap_aging",
        intent=4,
        quick_reference_hint="supplier statement → /report/supplier-statement-of-account",
        keyword_triggers=["supplier statement", "vendor statement of account"],
    ),
    RoutingRule(
        name="top_vendors",
        patterns=[
            re.compile(
                r"\b(?:top|bottom|least|lowest|worst)\s+(?:vendors?|suppliers?)\b|"
                r"\bpurchases by vendor\b|\btop suppliers by purchase\b",
                re.IGNORECASE,
            ),
            re.compile(r"(?:top|bottom|least|lowest|worst)\s+(\d+)\s+(?:vendors?|suppliers?)", re.IGNORECASE),
        ],
        endpoint="/report/purchases-by-vendor",
        sql_task="top_vendors",
        intent=4,
        quick_reference_hint="top vendors / purchases by supplier → /report/purchases-by-vendor",
        keyword_triggers=["top vendors", "bottom vendors", "top suppliers", "purchases by vendor"],
    ),
    RoutingRule(
        name="expense_by_category",
        patterns=[
            re.compile(r"\b(expenses? by category|spending breakdown|spending distribution across departments)\b", re.IGNORECASE),
            re.compile(r"expense(?:s)?\s+by\s+categor|(?:expense|spend)\s+breakdown", re.IGNORECASE),
        ],
        endpoint="/report/expense-by-category",
        sql_task="expense_by_category",
        intent=4,
        quick_reference_hint="expense by category / spending breakdown → /report/expense-by-category",
        keyword_triggers=["expense by category", "spending breakdown", "what are we spending on"],
    ),
    RoutingRule(
        name="bank_accounts",
        patterns=[
            re.compile(r"\b(cash balance|bank balance|how much cash|liquidity.*bank accounts?)\b", re.IGNORECASE),
            re.compile(r"(?:bank|cash)\s+balance|how\s+much\s+(?:cash|money)\s+(?:do\s+we\s+have|in\s+(?:the\s+)?bank)", re.IGNORECASE),
        ],
        endpoint="/bank/manual/accounts",
        sql_task="bank_balances",
        intent=4,
        quick_reference_hint="cash balance / bank balance → /bank/manual/accounts",
        keyword_triggers=["bank balance", "cash balance", "bank accounts"],
    ),
    RoutingRule(
        name="uncategorized_transactions",
        patterns=[
            re.compile(r"\b(uncategori[sz]ed(\s+bank)?(\s+transactions?)?)\b", re.IGNORECASE),
        ],
        endpoint="/bank/transactions/uncategorized",
        sql_task="uncategorized_transactions",
        intent=4,
        quick_reference_hint="uncategorized bank transactions → /bank/transactions/uncategorized",
        keyword_triggers=["uncategorized", "unassigned transactions"],
    ),
    RoutingRule(
        name="journal_entries",
        patterns=[
            re.compile(
                r"\b("
                r"journal\s+entries"
                r"|(?:what|which|show|list|any)\s+entr(?:y|ies)\s+(?:were\s+|was\s+)?(?:posted|recorded|created|made)"
                r"|entr(?:y|ies)\s+(?:that\s+were\s+)?(?:posted|recorded)"
                r")\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"journal\s+entr(?:y|ies)|entr(?:y|ies)\s+(?:posted|recorded)",
                re.IGNORECASE,
            ),
        ],
        endpoint="/accounting/journal-entries",
        sql_task="journal_entry_search",
        intent=4,
        quick_reference_hint="journal entries posted / recorded → /accounting/journal-entries",
        keyword_triggers=["journal entries", "entries posted", "entries recorded"],
    ),
    RoutingRule(
        name="chart_of_accounts",
        patterns=[
            re.compile(r"\b(chart of accounts|chart of accounts hierarchy)\b", re.IGNORECASE),
            re.compile(r"chart\s+of\s+accounts?|list\s+(?:all\s+)?accounts?", re.IGNORECASE),
        ],
        endpoint="/chart-of-accounts",
        sql_task="chart_of_accounts",
        intent=4,
        quick_reference_hint="chart of accounts / account codes → /chart-of-accounts",
        keyword_triggers=["chart of accounts"],
    ),
    RoutingRule(
        name="dashboard_overview",
        patterns=[
            re.compile(r"\b(business health|health check|how are we doing)\b", re.IGNORECASE),
        ],
        endpoint="/dashboard/web",
        sql_task="dashboard_overview",
        intent=7,
        quick_reference_hint="business health check / executive overview → /dashboard/web",
        keyword_triggers=["health check", "business health", "how are we doing"],
    ),
    RoutingRule(
        name="invoice_list",
        patterns=[
            re.compile(
                r"\b((list|show|all|recent)\s+(sales\s+|unpaid\s+|paid\s+|recent\s+)*invoices?|"
                r"invoices?\s+(list|for\s+(this|last|next|previous|current|q[1-4]|20\d{2})|by\s+(customer|client|status|date)))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/income/list",
        sql_task="overdue_invoices",
        intent=4,
        quick_reference_hint="list invoices / recent sales invoices → /income/list",
        keyword_triggers=["list invoices", "show invoices", "unpaid invoices"],
    ),
    RoutingRule(
        name="bill_list",
        patterns=[
            re.compile(
                r"\b((list|show|all|recent)\s+(vendor\s+|unpaid\s+|paid\s+)*bills?|"
                r"bills?\s+(list|for\s+(this|last|next|previous|current|q[1-4]|20\d{2})|by\s+(vendor|supplier|status|date)))\b",
                re.IGNORECASE,
            ),
        ],
        endpoint="/expense/list_filter",
        sql_task="overdue_bills",
        intent=4,
        quick_reference_hint="list bills / recent vendor bills → /expense/list_filter",
        keyword_triggers=["list bills", "show bills", "vendor bills"],
    ),
    RoutingRule(
        name="item_list",
        patterns=[
            re.compile(r"\b((list|show|all)\s+(our\s+)?(products?|items?)|products?\s+sorted)\b", re.IGNORECASE),
        ],
        endpoint="/item/list",
        sql_task="item_catalog",
        intent=4,
        quick_reference_hint="products / items catalog list → /item/list",
        keyword_triggers=["list items", "show products", "all items"],
    ),
    RoutingRule(
        name="project_expense_rollup",
        patterns=[
            re.compile(r"\b(project\s+expenses?|expenses?\s+by\s+project|project\s+spending)\b", re.IGNORECASE),
        ],
        endpoint="fn_project_expense_rollup",
        sql_task="project_expense_rollup",
        intent=4,
        quick_reference_hint="project expense rollup by vendor & account → fn_project_expense_rollup",
        keyword_triggers=["project expense", "project spending"],
    ),
    RoutingRule(
        name="inventory_movement",
        patterns=[
            re.compile(r"\b(inventory\s+movement|items?\s+with\s+warehouse|warehouse\s+location.*units\s+sold|units\s+sold.*units\s+dispatched)\b", re.IGNORECASE),
        ],
        endpoint="fn_inventory_movement",
        sql_task="inventory_movement",
        intent=4,
        quick_reference_hint="inventory movement across warehouses & invoices → fn_inventory_movement",
        keyword_triggers=["inventory movement"],
    ),
    RoutingRule(
        name="gl_profitability",
        patterns=[
            re.compile(r"\b(gl\s+profitability|general\s+ledger\s+(profit|profitability|margin)|profitability\s+by\s+account\s+type)\b", re.IGNORECASE),
        ],
        endpoint="fn_gl_profitability",
        sql_task="gl_profitability",
        intent=4,
        quick_reference_hint="GL account profitability analysis → fn_gl_profitability",
        keyword_triggers=["gl profitability", "general ledger margin"],
    ),
    RoutingRule(
        name="trial_balance",
        patterns=[
            re.compile(r"\b(trial\s+balance(\s+report)?)\b", re.IGNORECASE),
        ],
        endpoint="/report/trial-balance",
        sql_task="trial_balance",
        intent=3,
        quick_reference_hint="trial balance report → /report/trial-balance",
        keyword_triggers=["trial balance"],
    ),
]


def get_fast_router_rules() -> List[Tuple[Pattern, str, str, int]]:
    """Generate compiled FAST_ROUTER_RULES tuples (pattern, rule_name, endpoint, intent)."""
    rules = []
    for r in ROUTING_RULES:
        # Use primary pattern (first pattern in list)
        rules.append((r.patterns[0], r.name, r.endpoint, r.intent))
    return rules


def get_sql_fast_path_rules() -> List[Tuple[Pattern, str, Callable]]:
    """Generate compiled _FAST_PATH list for SQL fallback."""
    fast_paths = []
    for r in ROUTING_RULES:
        if not r.sql_task:
            continue

        # Choose appropriate pattern (pattern with capture group if available, else first pattern)
        pat = r.patterns[1] if len(r.patterns) > 1 else r.patterns[0]

        # Specific builders for SQL tasks
        if r.name == "income_total":
            builder = lambda m, oid: {"organization_id": oid, "filter_type": "YEARLY"}
        elif r.name == "expense_total":
            builder = lambda m, oid: {"organization_id": oid, "filter_type": "YEARLY"}
        elif r.name in ("sales_by_customer", "top_vendors"):
            builder = lambda m, oid: {
                "limit": _safe_int(m, 1, 10),
                "sort_order": "asc" if extract_direction_from_text(_query_text(m)) else "desc",
                "organization_id": oid,
            }
        elif r.name == "cash_forecast":
            builder = lambda m, oid: {
                "months": forecast_months(_query_text(m)),
                "organization_id": oid,
            }
        elif r.name in ("invoice_list", "bill_list"):
            builder = lambda m, oid: {
                "limit": extract_requested_count(_query_text(m), default=20),
                "organization_id": oid,
            }
        else:
            builder = lambda m, oid: {"organization_id": oid}

        fast_paths.append((pat, r.sql_task, builder))
    return fast_paths


#: Task names confirmed to exist in the external finance_agent's real dispatch
#: table (agents/finance_agent.py `handle()`, host coordinator codebase). A
#: rule's sql_task must be one of these to be usable for empty-result
#: verification — several rules reference task names that don't actually exist
#: there (renamed/never-implemented on the backend side: item_catalog,
#: project_expense_rollup, gl_profitability, uncategorized_transactions), which
#: would silently no-op if trusted blindly instead of being excluded here.
_VALID_FINANCE_AGENT_TASKS = frozenset({
    "execute_sql", "get_invoice_total", "get_expense_total", "list_invoices",
    "list_expenses", "top_customers", "top_vendors", "count_records",
    "get_invoice_details", "aggregate_metric", "trial_balance", "balance_sheet",
    "profit_and_loss", "general_ledger", "chart_of_accounts", "journal_entry_search",
    "bank_balances", "bank_transactions", "inventory_status", "inventory_valuation",
    "inventory_movements", "ar_aging", "ap_aging", "overdue_invoices", "overdue_bills",
    "audit_trail", "audit_activity", "project_profitability", "expense_by_category",
    "cost_center_breakdown", "customer_payments", "vendor_payments",
    "unallocated_payments", "payment_forecast", "reconciliation_status",
    "branch_summary", "cash_flow_summary", "invoice_status_summary",
    "bill_status_summary", "weekly_transaction_summary", "monthly_revenue_trend",
    "vat_summary", "recent_transactions", "customer_overdue_summary",
})


#: Hand-written verifiers for endpoints with no pre-built backend task at all,
#: so there's nothing in ROUTING_RULES to derive them from. Checked directly
#: against the real schema (accutax_bk_schema.json) before writing:
#:   - contacts.organization_id is varchar (quoted comparison, matches the
#:     `alias.organization_id = '{int(org_id)}'` convention already used
#:     throughout agents/finance_agent.py); contact_type_id 1/2/3 = vendor,
#:     4 = customer (documented in the coordinator's own system prompt).
#:   - income_items.line_amount is the actual sold-line total (numeric column,
#:     not the item's list cost) — the same income_items -> items -> income
#:     join finance_agent.py's own get_invoice_total task already uses.
#: Both are plain read-only SELECTs run via the real execute_sql task, with
#: the tenant filter embedded directly in the query text (execute_sql does not
#: apply tenant isolation itself, so it must be done here, not left implicit).
_EXTRA_SQL_VERIFIERS: Dict[str, Tuple[str, Callable[[Any, int], Dict[str, Any]]]] = {
    "/contact/list": ("execute_sql", lambda m, oid: {"sql": (
        "SELECT name, email, phone_number FROM contacts "
        f"WHERE organization_id = '{int(oid)}' AND contact_type_id IN (1,2,3) "
        "AND (is_deleted IS NOT TRUE) ORDER BY name LIMIT 50"
    )}),
    "/report/sales-by-items": ("execute_sql", lambda m, oid: {"sql": (
        "SELECT i.name AS item, COUNT(DISTINCT ii.income_id) AS invoice_count, "
        "SUM(ii.line_amount) AS total_sales "
        "FROM income_items ii JOIN items i ON i.id = ii.items_id "
        "JOIN income inc ON inc.id = ii.income_id "
        f"WHERE inc.organization_id = '{int(oid)}' "
        "GROUP BY i.name ORDER BY total_sales DESC LIMIT 10"
    )}),
}


def get_endpoint_sql_verifiers() -> Dict[str, Tuple[str, Callable[[Any, int], Dict[str, Any]]]]:
    """Map REST endpoint -> (sql_task, param_builder) for every rule that has a
    sql_task confirmed to exist on the backend, so a live API's EMPTY result
    from one of these specific endpoints can be cross-checked against a cheap,
    deterministic direct-SQL query before it's trusted as "confirmed zero
    records".

    Excluded, and left trusted as-is:
      - Endpoints with no sql_task at all (dashboards, static config/lookup
        lists, audit logs) — there's no cheap SQL equivalent to verify against.
      - Endpoints whose sql_task doesn't match a real backend task name (see
        _VALID_FINANCE_AGENT_TASKS) — claiming coverage there would be false.
      - fn_* endpoints — these call a separate Postgres-function execution path
        (execute_sql_function), not the finance_agent task dispatch this
        verifier uses, so none of the sql_task names here apply to them.
    """
    verifiers: Dict[str, Tuple[str, Callable]] = {}
    for r in ROUTING_RULES:
        if not r.sql_task or r.endpoint in verifiers or r.endpoint.startswith("fn_"):
            continue
        if r.sql_task not in _VALID_FINANCE_AGENT_TASKS:
            continue
        if r.name in ("income_total", "expense_total"):
            builder = lambda m, oid: {"organization_id": oid, "filter_type": "YEARLY"}
        elif r.name in ("sales_by_customer", "top_vendors"):
            patterns = r.patterns
            builder = lambda q, oid, patterns=patterns: {
                "limit": _extract_limit_from_query(q, patterns, 10),
                "sort_order": "asc" if extract_direction_from_text(q) else "desc",
                "organization_id": oid,
            }
        elif r.name == "cash_forecast":
            builder = lambda m, oid: {
                "months": forecast_months(_query_text(m)),
                "organization_id": oid,
            }
        elif r.name in ("invoice_list", "bill_list"):
            builder = lambda m, oid: {
                "limit": extract_requested_count(_query_text(m), default=20),
                "organization_id": oid,
            }
        elif r.name == "journal_entries":
            builder = lambda m, oid: {
                "organization_id": oid,
                "limit": 50,
                "filters": date_filters_from_request(phrase=_query_text(m)),
            }
        else:
            builder = lambda m, oid: {"organization_id": oid}
        verifiers[r.endpoint] = (r.sql_task, builder)
    verifiers.update(_EXTRA_SQL_VERIFIERS)
    return verifiers


def get_quick_reference_block() -> str:
    """Generate QUICK REFERENCE block text for LLM prompts."""
    hints = [r.quick_reference_hint for r in ROUTING_RULES if r.quick_reference_hint]
    return "QUICK REFERENCE — use these exact endpoints for these query types:\n  " + "\n  ".join(hints)

# ── Ageing-qualifier guard ───────────────────────────────────────────────────
# "top 5 vendors" and "top 5 vendors with overdue over 90 days" are different
# questions: the first ranks by spend, the second by what is still outstanding.
# The ranking rules have nowhere to put an ageing qualifier, so rather than
# answer a question the user did not ask, matches are redirected to the aging
# rule, which does express it.

AGING_QUALIFIER = re.compile(
    r"\b(overdue|past\s+due|outstanding|unpaid|aging|ageing|aged|arrears|"
    r"\d+\s*[-+]?\s*days?\b)",
    re.IGNORECASE,
)

#: Ranking rule/task → the aging rule/task to prefer when a qualifier is present.
RANKING_TO_AGING = {
    "top_vendors": "ap_aging",
    "top_customers": "ar_aging",
    "sales_by_customer": "ar_aging",
    "expense_by_category": "ap_aging",
}


def redirect_for_aging(rule_name: str, question: str) -> Optional[str]:
    """Return the aging rule that should answer instead, or None to keep going.

    Returns None when the rule is not a ranking rule, or when the question
    carries no ageing qualifier for it to drop.
    """
    if rule_name not in RANKING_TO_AGING:
        return None
    if not AGING_QUALIFIER.search(question):
        return None
    return RANKING_TO_AGING[rule_name]
