"""
Finance Agent — Financial data retrieval and deterministic metric computation.

Responsibilities:
- Execute analytics SQL against the active database
- Compute financial metrics (sum, count, avg, min, max)
- Fetch invoice details, expense records, contact lists
- Build and run SQL for structured queries
- General Ledger queries (journal entries, chart of accounts)
- Trial Balance, Balance Sheet, P&L reports
- Bank account balances and transactions
- Inventory status, movements, valuation
- AR/AP aging reports
- Audit trail queries
- Return FactCollection-style results

This agent DOES execute SQL (read-only).
It does NOT interpret tax.
It does NOT make predictions.
It returns raw, accurate financial data.
"""

import json
import logging
import re
from typing import Dict, Any, Optional, List
from decimal import Decimal
import datetime
import uuid

from gemini_brain.agents.executor import execute_sql
from gemini_brain.utils.ranking import order_sql
from gemini_brain.utils.ranking import resolve_direction as _direction
from gemini_brain.utils.ranking import resolve_limit as _resolve_limit

logger = logging.getLogger("agents.finance")


# ──────────────────────────────────────────────
# Tenant isolation safety net for free-form SQL
# ──────────────────────────────────────────────
# Every table in accutax_bk that carries an organization_id column. The
# execute_sql task lets the LLM write arbitrary SQL, so this is a
# defense-in-depth check on top of the structured tasks (which already
# inject organization_id safely via _maybe_org/_org_frag): force any
# organization_id literal in the generated SQL to match the verified
# session org_id, and inject a filter if a tenant table is queried with
# no org filter at all. Mirrors the equivalent hardening in Gemini_Brain's
# sql_fallback/sql_engine.py.
TENANT_TABLES = {
    "audit_logs", "audit_trails", "bank_accounts", "bank_transaction_rules",
    "bank_transactions", "branches", "chart_of_accounts", "contacts",
    "cost_centers", "customer_overdue_summary", "customer_payment", "expense",
    "income", "inventory_adjustments", "inventory_fifo_layers", "inventory_ledger",
    "inventory_movements", "inventory_quantities", "inventory_transfers",
    "journal_entries", "overdue_invoices", "projects", "reconciliations",
    "sub_contacts", "supplier_payments", "warehouses",
}


def _enforce_tenant_isolation_sql(sql: str, organization_id) -> str:
    """Best-effort regex hardening of LLM-generated SQL against the verified org_id."""
    if organization_id is None:
        return sql
    try:
        org_id = int(organization_id)
    except (TypeError, ValueError):
        return sql

    # 1. Force-correct any organization_id literal to the verified value,
    #    regardless of what the model wrote (catches wrong or spoofed values).
    fixed = re.sub(
        r'\borganization_id\s*=\s*\d+\b',
        f'organization_id = {org_id}',
        sql,
        flags=re.IGNORECASE,
    )

    # 2. If a known tenant table is referenced with no organization_id filter
    #    anywhere in the query, inject one (catches the filter being omitted
    #    entirely).
    has_org_filter = re.search(r'\borganization_id\s*=', fixed, re.IGNORECASE) is not None
    touches_tenant_table = any(
        re.search(rf'\b{re.escape(t)}\b', fixed, re.IGNORECASE) for t in TENANT_TABLES
    )

    if touches_tenant_table and not has_org_filter:
        stripped = fixed.strip().rstrip(';')
        if re.search(r'\bwhere\b', stripped, re.IGNORECASE):
            stripped = re.sub(
                r'\bWHERE\b',
                f'WHERE organization_id = {org_id} AND',
                stripped, count=1, flags=re.IGNORECASE,
            )
        else:
            m = re.search(r'\b(GROUP BY|ORDER BY|LIMIT)\b', stripped, re.IGNORECASE)
            if m:
                stripped = stripped[:m.start()] + f'WHERE organization_id = {org_id} ' + stripped[m.start():]
            else:
                stripped += f' WHERE organization_id = {org_id}'
        if stripped != fixed:
            logger.warning("execute_sql: injected missing organization_id filter (org=%s)", org_id)
        fixed = stripped

    if fixed != sql:
        logger.info("execute_sql: tenant-isolation rewrite applied (org=%s)", org_id)
    return fixed


# ──────────────────────────────────────────────
# Cost extraction helper (items.cost is VARCHAR)
# ──────────────────────────────────────────────
COST_EXPR = "NULLIF(REGEXP_REPLACE(i.cost, '[^0-9\\.]', '', 'g'), '')::NUMERIC"


def _serialize_value(v):
    """Convert any non-JSON-safe PostgreSQL value to a JSON-safe primitive."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, datetime.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    if isinstance(v, (int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_serialize_value(i) for i in v]
    if isinstance(v, dict):
        return {k: _serialize_value(val) for k, val in v.items()}
    # Fallback: cast to string
    return str(v)


def _run_sql(sql: str) -> Dict[str, Any]:
    """Execute SQL and return structured result with all values JSON-safe."""
    try:
        cols, rows = execute_sql(sql)
        total_rows = len(rows)
        # Cap results to prevent massive payloads
        if total_rows > 100:
            rows = rows[:100]
        results = [
            {k: _serialize_value(v) for k, v in zip(cols, row)}
            for row in rows
        ]
        return {"success": True, "sql": sql, "results": results, "row_count": total_rows}
    except Exception as e:
        return {"success": False, "sql": sql, "error": str(e), "results": [], "row_count": 0}


# ──────────────────────────────────────────────
# Public API — called by Coordinator as tool
# ──────────────────────────────────────────────

def handle(task: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Finance Agent entry point.

    Supported tasks:
      ═══ Original ═══
      - execute_sql          → run raw SQL (must be SELECT only)
      - get_invoice_total    → total amount for invoices matching filters
      - get_expense_total    → total amount for expenses matching filters
      - list_invoices        → list invoices with optional filters
      - list_expenses        → list expenses with optional filters
      - top_customers        → top N customers by revenue
      - top_vendors          → top N vendors by spend
      - count_records        → count records in an entity
      - get_invoice_details  → full details for a specific invoice
      - aggregate_metric     → flexible metric (sum/count/avg) on entity

      ═══ Accounting Core (NEW) ═══
      - trial_balance        → trial balance report from journal entries
      - balance_sheet        → assets, liabilities, equity summary
      - profit_and_loss      → revenue minus expenses from GL
      - general_ledger       → ledger activity for an account or date range
      - chart_of_accounts    → list all accounts with types and balances
      - journal_entry_search → search journal entries by ref, date, amount

      ═══ Banking (NEW) ═══
      - bank_balances        → all bank account balances
      - bank_transactions    → list bank transactions with filters

      ═══ Inventory (NEW) ═══
      - inventory_status     → current stock levels per item/warehouse
      - inventory_valuation  → stock value (qty × cost) per item
      - inventory_movements  → stock in/out events

      ═══ AR/AP (NEW) ═══
      - ar_aging             → accounts receivable aging buckets
      - ap_aging             → accounts payable aging buckets

      ═══ Audit (NEW) ═══
      - audit_trail          → who changed what, when (field-level)
      - audit_activity       → API activity log

      ═══ Advanced (NEW) ═══
      - project_profitability → revenue minus cost per project
      - expense_by_category   → expenses grouped by category
      - cost_center_breakdown → expenses per cost center

      ═══ Payments (NEW) ═══
      - customer_payments      → list customer payment receipts
      - vendor_payments        → list supplier payment outflows
      - unallocated_payments   → payments not fully applied to invoices/bills
      - payment_forecast       → upcoming payments due in N days

      ═══ Reconciliation & Structure (NEW) ═══
      - reconciliation_status  → bank reconciliation status from reconciliations table
      - branch_summary         → list branches for the organization
      - cash_flow_summary      → categorized bank cash inflow/outflow summary
      - invoice_status_summary → invoice count/amount breakdown by status
      - bill_status_summary    → bill count/amount breakdown by status
    """
    params = params or {}

    dispatch = {
        # Original tasks
        "execute_sql":         _task_execute_sql,
        "get_invoice_total":   _task_invoice_total,
        "get_expense_total":   _task_expense_total,
        "list_invoices":       _task_list_invoices,
        "list_expenses":       _task_list_expenses,
        "top_customers":       _task_top_customers,
        "top_vendors":         _task_top_vendors,
        "count_records":       _task_count_records,
        "get_invoice_details": _task_invoice_details,
        "aggregate_metric":    _task_aggregate_metric,
        # Accounting Core
        "trial_balance":       _task_trial_balance,
        "balance_sheet":       _task_balance_sheet,
        "profit_and_loss":     _task_profit_and_loss,
        "general_ledger":      _task_general_ledger,
        "chart_of_accounts":   _task_chart_of_accounts,
        "journal_entry_search": _task_journal_entry_search,
        # Banking
        "bank_balances":       _task_bank_balances,
        "bank_transactions":   _task_bank_transactions,
        # Inventory
        "inventory_status":    _task_inventory_status,
        "inventory_valuation": _task_inventory_valuation,
        "inventory_movements": _task_inventory_movements,
        # AR/AP
        "ar_aging":            _task_ar_aging,
        "ap_aging":            _task_ap_aging,
        "overdue_invoices":    _task_overdue_invoices,
        "overdue_bills":       _task_overdue_bills,
        # Audit
        "audit_trail":         _task_audit_trail,
        "audit_activity":      _task_audit_activity,
        # Advanced
        "project_profitability": _task_project_profitability,
        "expense_by_category":   _task_expense_by_category,
        "cost_center_breakdown": _task_cost_center_breakdown,
        # Payments
        "customer_payments":     _task_customer_payments,
        "vendor_payments":       _task_vendor_payments,
        "unallocated_payments":  _task_unallocated_payments,
        "payment_forecast":      _task_payment_forecast,
        # Reconciliation & Structure
        "reconciliation_status": _task_reconciliation_status,
        "branch_summary":        _task_branch_summary,
        "cash_flow_summary":     _task_cash_flow_summary,
        "invoice_status_summary": _task_invoice_status_summary,
        "bill_status_summary":   _task_bill_status_summary,
        # Additional tasks for common question patterns
        "weekly_transaction_summary": _task_weekly_transaction_summary,
        "monthly_revenue_trend":      _task_monthly_revenue_trend,
        "vat_summary":                _task_vat_summary,
        "recent_transactions":        _task_recent_transactions,
        "customer_overdue_summary":   _task_customer_overdue_summary,
    }

    handler = dispatch.get(task)
    if handler:
        try:
            return handler(params)
        except Exception as e:
            logger.exception(f"Finance agent task '{task}' failed")
            return {"error": f"Finance agent error: {str(e)}"}
    return {"error": f"Unknown finance_agent task: {task}. Available: {list(dispatch.keys())}"}


# ══════════════════════════════════════════════
# Filter / WHERE builder
# ══════════════════════════════════════════════

import datetime as _dt

def _safe_year(val) -> int:
    """Resolve year value — handles 'current', 'this_year', lists, etc.
    NOTE: If a list is passed (e.g. [2024, 2025]), returns the FIRST element.
    The LLM should make separate profit_and_loss calls per year, not pass a list.
    """
    if isinstance(val, list):
        # Received a list — use first element so data isn't silently redirected to current year
        return _safe_year(val[0]) if val else _dt.date.today().year
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().lower()
    if s.isdigit():
        return int(s)
    # 'current', 'this_year', 'now' etc. → current year
    return _dt.date.today().year

def _safe_month(val) -> int:
    """Resolve month value — handles 'current', 'this_month', etc."""
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().lower()
    if s.isdigit():
        return int(s)
    return _dt.date.today().month

def _build_where(filters: Dict, entity: str = "income", params: Dict = None) -> tuple:
    """Build WHERE clauses and joins from filter dict."""
    clauses = []
    joins = []
    if entity == "income":
        alias = "inc"
    elif entity in ("contact", "contacts", "customer", "vendor"):
        alias = "c"
    else:
        alias = "e"

    if entity in ("contact", "contacts", "customer", "vendor"):
        clauses.append(f"{alias}.is_deleted = false")

    _maybe_org(clauses, alias, params or {})

    if filters.get("customer"):
        joins.append(f"JOIN contacts c ON c.id = {alias}.contact_id")
        clauses.append(f"LOWER(c.name) LIKE '%{filters['customer'].lower()}%'")
    elif filters.get("vendor"):
        joins.append(f"JOIN contacts c ON c.id = {alias}.contact_id")
        clauses.append(f"LOWER(c.name) LIKE '%{filters['vendor'].lower()}%'")

    if filters.get("invoice_number"):
        clauses.append(f"{alias}.invoice_number = '{filters['invoice_number']}'")

    if filters.get("status"):
        joins.append(f"LEFT JOIN status_type st ON st.id = {alias}.status_type_id")
        status_val = filters["status"]
        if isinstance(status_val, list):
            status_list = ", ".join(f"'{s.lower()}'" for s in status_val)
            clauses.append(f"LOWER(st.value) IN ({status_list})")
        else:
            clauses.append(f"LOWER(st.value) = '{status_val.lower()}'")

    if filters.get("date_from"):
        date_col = "invoice_date" if entity == "income" else "reception_date"
        clauses.append(f"CAST({alias}.{date_col} AS DATE) >= '{filters['date_from']}'")
    if filters.get("date_to"):
        date_col = "invoice_date" if entity == "income" else "reception_date"
        clauses.append(f"CAST({alias}.{date_col} AS DATE) <= '{filters['date_to']}'")

    if filters.get("year"):
        date_col = "invoice_date" if entity == "income" else "reception_date"
        clauses.append(f"EXTRACT(YEAR FROM CAST({alias}.{date_col} AS DATE)) = {_safe_year(filters['year'])}")

    if filters.get("month"):
        date_col = "invoice_date" if entity == "income" else "reception_date"
        clauses.append(f"EXTRACT(MONTH FROM CAST({alias}.{date_col} AS DATE)) = {_safe_month(filters['month'])}")

    return clauses, joins


def _build_date_filter(alias: str, date_col: str, filters: Dict) -> List[str]:
    """Generic date filter builder for any table."""
    clauses = []
    if filters.get("date_from"):
        clauses.append(f"CAST({alias}.{date_col} AS DATE) >= '{filters['date_from']}'")
    if filters.get("date_to"):
        clauses.append(f"CAST({alias}.{date_col} AS DATE) <= '{filters['date_to']}'")
    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST({alias}.{date_col} AS DATE)) = {_safe_year(filters['year'])}")
    if filters.get("month"):
        clauses.append(f"EXTRACT(MONTH FROM CAST({alias}.{date_col} AS DATE)) = {_safe_month(filters['month'])}")
    return clauses


def _maybe_org(clauses: list, alias: str, params: Dict):
    """Add organization_id filter to clauses list if org_id is in params."""
    org_id = params.get("organization_id")
    if org_id is not None:
        clauses.append(f"{alias}.organization_id = '{int(org_id)}'")


def _org_frag(alias: str, params: Dict) -> str:
    """Return '\n  AND alias.organization_id = N' fragment, or empty string."""
    org_id = params.get("organization_id")
    if org_id is None:
        return ""
    return f"\n  AND {alias}.organization_id = '{int(org_id)}'"



# ══════════════════════════════════════════════
# ORIGINAL TASKS (unchanged logic)
# ══════════════════════════════════════════════

def _task_execute_sql(params: Dict) -> Dict:
    """Run arbitrary read-only SQL."""
    sql = params.get("sql", "")
    if not sql.strip():
        return {"error": "No SQL provided"}
    # Tenant isolation safety net — see _enforce_tenant_isolation_sql for why
    # this is needed even though the caller already passes a verified org_id.
    sql = _enforce_tenant_isolation_sql(sql, params.get("organization_id"))
    # Safety: add LIMIT if not present to prevent massive result sets
    sql_stripped = sql.strip().rstrip(';')
    if 'limit' not in sql_stripped.lower().split(')')[-1]:
        sql_stripped += ' LIMIT 100'
    return _run_sql(sql_stripped)


def _task_invoice_total(params: Dict) -> Dict:
    """Get total invoice amount with optional filters."""
    filters = params.get("filters", {})
    clauses, extra_joins = _build_where(filters, "income", params)

    joins = [
        "JOIN income_items ii ON ii.income_id = inc.id",
    ] + extra_joins

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT COALESCE(SUM(ii.line_amount), 0) AS total
FROM income inc
{chr(10).join(joins)}
{where_str}"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["total"] = result["results"][0].get("total", 0)
    return result


def _task_expense_total(params: Dict) -> Dict:
    """Get total expense amount with optional filters."""
    filters = params.get("filters", {})
    clauses, extra_joins = _build_where(filters, "expense", params)

    joins = [
        "JOIN expense_items ei ON ei.expense_id = e.id",
    ] + extra_joins

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT COALESCE(SUM(ei.line_amount), 0) AS total
FROM expense e
{chr(10).join(joins)}
{where_str}"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["total"] = result["results"][0].get("total", 0)
    return result


def _task_list_invoices(params: Dict) -> Dict:
    """List invoices with optional filters."""
    filters = params.get("filters", {})
    limit = params.get("limit", 100)
    clauses, extra_joins = _build_where(filters, "income", params)

    base_joins = ["LEFT JOIN contacts c ON c.id = inc.contact_id"]
    if any("contacts c" in j for j in extra_joins):
        base_joins = []
    all_joins = base_joins + extra_joins

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT inc.invoice_date, inc.invoice_number, COALESCE(c.name, 'Unknown') AS customer
FROM income inc
{chr(10).join(all_joins)}
{where_str}
ORDER BY inc.invoice_date DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_list_expenses(params: Dict) -> Dict:
    """List expenses with optional filters."""
    filters = params.get("filters", {})
    limit = params.get("limit", 100)
    clauses, extra_joins = _build_where(filters, "expense", params)

    base_joins = ["LEFT JOIN contacts c ON c.id = e.contact_id"]
    if any("contacts c" in j for j in extra_joins):
        base_joins = []
    all_joins = base_joins + extra_joins

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT e.reception_date, e.expense_type, COALESCE(c.name, 'Unknown') AS vendor, e.receipt_number
FROM expense e
{chr(10).join(all_joins)}
{where_str}
ORDER BY e.reception_date DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_top_customers(params: Dict) -> Dict:
    """Top (or bottom, via sort_order) N customers by total invoice revenue."""
    n = _resolve_limit(params, default=10, ceiling=50)
    order = order_sql(_direction(params, raw_query=params.get("query", "")))
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "inc", params)

    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(inc.invoice_date AS DATE)) = {_safe_year(filters['year'])}")
    if filters.get("date_from"):
        clauses.append(f"CAST(inc.invoice_date AS DATE) >= '{filters['date_from']}'")
    if filters.get("date_to"):
        clauses.append(f"CAST(inc.invoice_date AS DATE) <= '{filters['date_to']}'")

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT COALESCE(c.name, c.organization_name, 'Unknown') AS customer,
       COUNT(DISTINCT inc.id) AS invoice_count,
       COALESCE(SUM(ii.line_amount), 0) AS total_revenue
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
{where_str}
GROUP BY c.organization_name, c.name
ORDER BY total_revenue {order}
LIMIT {int(n)}"""
    return _run_sql(sql)


def _task_top_vendors(params: Dict) -> Dict:
    """Top (or bottom, via sort_order) N vendors by total expense spend."""
    n = _resolve_limit(params, default=10, ceiling=50)
    order = order_sql(_direction(params, raw_query=params.get("query", "")))
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "e", params)

    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(e.reception_date AS DATE)) = {_safe_year(filters['year'])}")

    where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""SELECT COALESCE(c.name, c.organization_name, 'Unknown') AS vendor,
       COUNT(DISTINCT e.id) AS bill_count,
       COALESCE(SUM(ei.line_amount), 0) AS total_spend
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
{where_str}
GROUP BY c.organization_name, c.name
ORDER BY total_spend {order}
LIMIT {int(n)}"""
    return _run_sql(sql)


def _task_count_records(params: Dict) -> Dict:
    """Count records for an entity."""
    entity = params.get("entity", "income")
    filters = params.get("filters", {})

    table_map = {
        "income": ("income", "inc"),
        "invoice": ("income", "inc"),
        "expense": ("expense", "e"),
        "bill": ("expense", "e"),
        "contact": ("contacts", "c"),
        "customer": ("contacts", "c"),
        "vendor": ("contacts", "c"),
        "item": ("items", "itm"),
        "bank_account": ("bank_accounts", "ba"),
        "bank_transaction": ("bank_transactions", "bt"),
        "journal_entry": ("journal_entries", "je"),
        "journal_entry_line": ("journal_entry_lines", "jel"),
        "chart_of_account": ("chart_of_accounts", "coa"),
        "audit_trail": ("audit_trails", "atr"),
        "audit_log": ("audit_logs", "al"),
        "project": ("projects", "prj"),
        "cost_center": ("cost_centers", "cc"),
        "warehouse": ("warehouses", "wh"),
        "organization": ("organizations", "org"),
        "collaborator": ("collaborators", "collab"),
        "document": ("documents", "doc"),
    }

    entry = table_map.get(entity, ("income", "inc"))
    table, alias = entry
    clauses = []

    schema_ = None
    try:
        from gemini_brain.agents.schema_agent import get_schema
        schema_ = get_schema()
    except Exception:
        pass

    if schema_ and table in schema_ and schema_[table].get("has_is_deleted") and table in ("contacts", "sub_contacts"):
        clauses.append(f"{alias}.is_deleted = false")

    _maybe_org(clauses, alias, params)

    if entity == "customer":
        clauses.append(f"{alias}.contact_type_id = 4")
    elif entity == "vendor":
        clauses.append(f"{alias}.contact_type_id IN (1,2,3)")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT COUNT(*) AS total FROM {table} {alias} {where}"
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["count"] = result["results"][0].get("total", 0)
    return result


def _task_invoice_details(params: Dict) -> Dict:
    """Get full details for a specific invoice by number."""
    inv_num = params.get("invoice_number", "")
    if not inv_num:
        return {"error": "invoice_number is required"}

    # No org filter — invoice_number is unique enough, and the invoice may belong
    # to a sibling org in the same dataset (e.g. org 199 vs 175 in accutax_bk).
    sql = f"""SELECT inc.id, inc.invoice_number,
       inc.invoice_date, inc.due_date,
       COALESCE(c.name, 'Unknown') AS customer,
       c.email AS customer_email, c.phone_number AS customer_phone,
       COALESCE(st.value, 'Unknown') AS status,
       i.name AS item_name,
       ii.line_amount AS line_amount,
       ii.quantity, ii.unit_price
FROM income inc
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN items i ON i.id = ii.items_id
WHERE inc.invoice_number = '{inv_num}'
ORDER BY i.name"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        total = sum(r.get("line_amount") or 0 for r in result["results"])
        result["summary"] = {
            "invoice_number": inv_num,
            "customer": result["results"][0].get("customer"),
            "status": result["results"][0].get("status"),
            "invoice_date": result["results"][0].get("invoice_date"),
            "due_date": result["results"][0].get("due_date"),
            "total_amount": total,
            "line_items": len(result["results"]),
        }
    return result


def _task_aggregate_metric(params: Dict) -> Dict:
    """Flexible aggregation: sum/count/avg on income or expense."""
    metric = params.get("metric", "sum")
    entity = params.get("entity", "income")
    filters = params.get("filters", {})

    if entity in ("income", "invoice", "revenue"):
        clauses, extra_joins = _build_where(filters, "income", params)
        joins = [
            "JOIN income_items ii ON ii.income_id = inc.id",
        ] + extra_joins

        if metric == "count":
            select = "COUNT(DISTINCT inc.id) AS value"
        elif metric == "avg":
            select = "AVG(ii.line_amount) AS value"
        else:
            select = "COALESCE(SUM(ii.line_amount), 0) AS value"

        where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""SELECT {select}
FROM income inc
{chr(10).join(joins)}
{where_str}"""

    elif entity in ("expense", "bill"):
        clauses, extra_joins = _build_where(filters, "expense", params)
        joins = [
            "JOIN expense_items ei ON ei.expense_id = e.id",
        ] + extra_joins

        if metric == "count":
            select = "COUNT(DISTINCT e.id) AS value"
        elif metric == "avg":
            select = "AVG(ei.line_amount) AS value"
        else:
            select = "COALESCE(SUM(ei.line_amount), 0) AS value"

        where_str = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""SELECT {select}
FROM expense e
{chr(10).join(joins)}
{where_str}"""

    else:
        return {"error": f"Unsupported entity for aggregate: {entity}"}

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["value"] = result["results"][0].get("value", 0)
        result["metric"] = metric
        result["entity"] = entity
    return result


# ══════════════════════════════════════════════
# ACCOUNTING CORE TASKS (NEW)
# ══════════════════════════════════════════════

def _task_trial_balance(params: Dict) -> Dict:
    """
    Trial Balance from journal_entry_lines grouped by account.
    Uses denormalized account_code/account_name on journal_entry_lines.
    Optionally filtered by date range, year.
    """
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "je", params)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT jel.account_code,
       jel.account_name,
       COALESCE(SUM(jel.debit_amount), 0) AS total_debit,
       COALESCE(SUM(jel.credit_amount), 0) AS total_credit,
       COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0) AS net_balance
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
{where}
GROUP BY jel.account_code, jel.account_name
ORDER BY jel.account_code"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        total_debit = sum(r.get("total_debit", 0) for r in result["results"])
        total_credit = sum(r.get("total_credit", 0) for r in result["results"])
        result["summary"] = {
            "total_debit": total_debit,
            "total_credit": total_credit,
            "difference": total_debit - total_credit,
            "is_balanced": abs(total_debit - total_credit) < 0.01,
            "account_count": len(result["results"]),
        }
    return result


def _task_balance_sheet(params: Dict) -> Dict:
    """
    Balance Sheet summary: group by chart_of_accounts.account_type (VARCHAR).
    Typical values: Asset, Liability, Equity, Revenue, Expense.
    """
    filters = params.get("filters", {})
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    _maybe_org(date_clauses, "je", params)
    where = f"WHERE {' AND '.join(date_clauses)}" if date_clauses else ""

    sql = f"""SELECT
    COALESCE(coa.account_type, 'Unknown') AS account_type,
    COALESCE(SUM(jel.debit_amount), 0) AS total_debit,
    COALESCE(SUM(jel.credit_amount), 0) AS total_credit,
    COALESCE(SUM(jel.debit_amount), 0) - COALESCE(SUM(jel.credit_amount), 0) AS net_balance
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
{where}
GROUP BY coa.account_type
ORDER BY coa.account_type"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        type_totals = {r["account_type"]: r["net_balance"] for r in result["results"]}
        assets = type_totals.get("Asset", 0)
        liabilities = type_totals.get("Liability", 0)
        equity = type_totals.get("Equity", 0)
        result["summary"] = {
            "total_assets": assets,
            "total_liabilities": abs(liabilities),
            "total_equity": abs(equity),
            "accounting_equation": f"Assets ({assets:,.2f}) = Liabilities ({abs(liabilities):,.2f}) + Equity ({abs(equity):,.2f})",
            "balanced": abs(assets - (abs(liabilities) + abs(equity))) < 1.0,
        }
    return result


def _task_profit_and_loss(params: Dict) -> Dict:
    """
    P&L (Income Statement) from journal entries.
    Revenue = credit-normal accounts (account_type containing 'Revenue'/'Income')
    Expenses = debit-normal accounts (account_type containing 'Expense'/'Cost')
    CRITICAL: Only include Revenue and Expense account types, NOT Asset/Liability/Equity.
    """
    filters = params.get("filters", {})
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    _maybe_org(date_clauses, "je", params)

    # Build a human-readable period label so LLM can never mix up results
    if filters.get("year"):
        period_label = f"Year {_safe_year(filters['year'])}"
    elif filters.get("date_from") and filters.get("date_to"):
        period_label = f"{filters['date_from']} to {filters['date_to']}"
    elif filters.get("date_from"):
        period_label = f"From {filters['date_from']}"
    else:
        period_label = "All periods"

    # Filter to ONLY P&L account types (Revenue/Income and Expense/Cost of Goods)
    account_filter = """LOWER(COALESCE(coa.account_type, '')) IN (
        'revenue', 'income', 'other income', 'expense', 'other expense',
        'cost of goods sold', 'cost of sales', 'cogs', 'operating expense'
    )"""
    if date_clauses:
        where = f"WHERE {' AND '.join(date_clauses)} AND {account_filter}"
    else:
        where = f"WHERE {account_filter}"

    sql = f"""SELECT jel.account_code,
       jel.account_name,
       COALESCE(coa.account_type, 'Unknown') AS account_type,
       COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0) AS net_amount
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
{where}
GROUP BY jel.account_code, jel.account_name, coa.account_type
HAVING ABS(COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0)) > 0.01
ORDER BY coa.account_type, jel.account_code"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        # Revenue accounts have positive net_amount (credit > debit)
        # Expense accounts have negative net_amount (debit > credit)
        revenue_types = ('revenue', 'income', 'other income')
        expense_types = ('expense', 'other expense', 'cost of goods sold', 'cost of sales', 'cogs', 'operating expense')

        revenue = sum(
            r["net_amount"] for r in result["results"]
            if r.get("account_type", "").lower() in revenue_types
        )
        expenses = abs(sum(
            r["net_amount"] for r in result["results"]
            if r.get("account_type", "").lower() in expense_types
        ))

        # Fallback: if no account_type match, use sign-based detection
        if revenue == 0 and expenses == 0:
            revenue = sum(r["net_amount"] for r in result["results"] if r["net_amount"] > 0)
            expenses = abs(sum(r["net_amount"] for r in result["results"] if r["net_amount"] < 0))

        result["period"] = period_label
        result["summary"] = {
            "period": period_label,
            "total_revenue": revenue,
            "total_expenses": expenses,
            "net_profit": revenue - expenses,
            "profit_margin_pct": round((revenue - expenses) / revenue * 100, 2) if revenue else 0,
        }
    elif result["success"] and not result["results"]:
        # No P&L account types found, try a broader approach using account names
        fallback_sql = f"""SELECT jel.account_code,
       jel.account_name,
       COALESCE(coa.account_type, 'Unknown') AS account_type,
       COALESCE(SUM(jel.credit_amount), 0) AS total_credit,
       COALESCE(SUM(jel.debit_amount), 0) AS total_debit,
       COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0) AS net_amount
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
{f"WHERE {' AND '.join(date_clauses)}" if date_clauses else ""}
GROUP BY jel.account_code, jel.account_name, coa.account_type
HAVING ABS(COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0)) > 0.01
ORDER BY coa.account_type, jel.account_code"""
        fallback = _run_sql(fallback_sql)
        if fallback["success"] and fallback["results"]:
            # Classify based on account_type or name heuristics
            revenue = 0
            expenses = 0
            for r in fallback["results"]:
                at = (r.get("account_type") or "").lower()
                an = (r.get("account_name") or "").lower()
                if any(k in at or k in an for k in ('revenue', 'income', 'sales')):
                    revenue += r["net_amount"]
                elif any(k in at or k in an for k in ('expense', 'cost', 'rent', 'salary', 'utilities', 'depreciation')):
                    expenses += abs(r["net_amount"])
            result = fallback
            result["period"] = period_label
            result["summary"] = {
                "period": period_label,
                "total_revenue": revenue,
                "total_expenses": expenses,
                "net_profit": revenue - expenses,
                "profit_margin_pct": round((revenue - expenses) / revenue * 100, 2) if revenue else 0,
                "note": "P&L computed using account name heuristics — verify account_type mappings",
            }
    return result


def _task_general_ledger(params: Dict) -> Dict:
    """
    General Ledger: detailed journal entry lines for a specific account or date range.
    """
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "je", params)

    account_name = params.get("account_name", "")
    account_code = params.get("account_code", "")
    if account_name:
        clauses.append(f"LOWER(jel.account_name) LIKE '%{account_name.lower()}%'")
    if account_code:
        clauses.append(f"jel.account_code = '{account_code}'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT je.reference_number, je.transaction_date,
       jel.account_code, jel.account_name,
       jel.debit_amount, jel.credit_amount, jel.description AS line_description,
       je.description AS journal_description
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
{where}
ORDER BY je.transaction_date DESC, je.reference_number
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_chart_of_accounts(params: Dict) -> Dict:
    """List all chart of accounts with type and balance."""
    account_type = params.get("account_type", "")
    clauses = []
    if account_type:
        clauses.append(f"LOWER(coa.account_type) = '{account_type.lower()}'")
    _maybe_org(clauses, "coa", params)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT coa.id, coa.account_code, coa.account_name,
       coa.account_type, coa.account_sub_type,
       coa.balance, coa.parent_account_id, coa.is_active
FROM chart_of_accounts coa
{where}
ORDER BY coa.account_code"""
    return _run_sql(sql)


def _task_journal_entry_search(params: Dict) -> Dict:
    """Search journal entries by reference, date, amount, or description."""
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "je", params)

    if params.get("reference"):
        clauses.append(f"LOWER(je.reference_number) LIKE '%{params['reference'].lower()}%'")
    if params.get("notes") or params.get("description"):
        term = params.get("notes") or params.get("description")
        clauses.append(f"LOWER(je.description) LIKE '%{term.lower()}%'")
    if params.get("min_amount"):
        clauses.append(f"je.total_debit >= {float(params['min_amount'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT je.id, je.journal_number, je.reference_number, je.transaction_date,
       je.description, je.is_posted,
       je.total_debit, je.total_credit,
       (SELECT COUNT(*) FROM journal_entry_lines jel WHERE jel.journal_entry_id = je.id) AS line_count
FROM journal_entries je
{where}
ORDER BY je.transaction_date DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# BANKING TASKS (NEW)
# ══════════════════════════════════════════════

def _task_bank_balances(params: Dict) -> Dict:
    """Get all bank account balances."""
    org_id = params.get("organization_id")
    org_clause = f" AND ba.organization_id = '{int(org_id)}'" if org_id else ""
    sql = f"""SELECT ba.account_name, ba.bank_name,
       ba.balance AS current_balance,
       ba.opening_bank_balance,
       ba.currency_code,
       ba.is_active
FROM bank_accounts ba
WHERE (ba.is_active = true OR ba.balance IS NOT NULL){org_clause}
ORDER BY ba.currency_code, ba.balance DESC"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        # Group by currency
        by_currency = {}
        for r in result["results"]:
            cur = r.get("currency_code") or "AED"
            amt = r.get("current_balance", 0) or 0
            by_currency[cur] = by_currency.get(cur, 0) + amt
        result["summary"] = {
            "account_count": len(result["results"]),
            "by_currency": by_currency,
            "total_aed": by_currency.get("AED", 0),
        }
    return result
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["summary"] = {
            "account_count": len(result["results"]),
            "total_balance": sum(r.get("current_balance", 0) or 0 for r in result["results"]),
        }
    return result


def _task_bank_transactions(params: Dict) -> Dict:
    """List bank transactions with optional filters."""
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("bt", "date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "bt", params)

    if params.get("bank_account"):
        clauses.append(f"LOWER(bt.account_name) LIKE '%{params['bank_account'].lower()}%'")
    if params.get("category"):
        clauses.append(f"LOWER(bt.category) LIKE '%{params['category'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT bt.date, bt.description, bt.amount, bt.category,
       bt.account_name, bt.debit_or_credit
FROM bank_transactions bt
{where}
ORDER BY bt.date DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# INVENTORY TASKS (schema-aware — checks table existence)
# ══════════════════════════════════════════════

def _check_table_exists(table_name: str) -> bool:
    """Check if a table exists in the current database."""
    try:
        result = _run_sql(f"SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='{table_name}'")
        return result.get("success") and result.get("row_count", 0) > 0
    except Exception:
        return False


def _task_inventory_status(params: Dict) -> Dict:
    """Current stock levels per item per warehouse."""
    if not _check_table_exists("inventory_quantities"):
        return {"success": True, "results": [], "row_count": 0,
                "note": "No inventory_quantities table in this database. Inventory tracking is not enabled."}

    clauses = []
    _maybe_org(clauses, "iq", params)
    if params.get("item_name"):
        clauses.append(f"LOWER(itm.name) LIKE '%{params['item_name'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT itm.name AS item_name, itm.number AS item_number,
       iq.quantity_available, iq.quantity_reserved, iq.quantity_on_hold,
       iq.quantity_in_transit, iq.quantity_damaged,
       wh.warehouse_name
FROM inventory_quantities iq
JOIN items itm ON itm.id = iq.item_id
LEFT JOIN warehouses wh ON wh.id = iq.warehouse_id
{where}
ORDER BY itm.name
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_inventory_valuation(params: Dict) -> Dict:
    """Stock value: quantity × item cost for each item."""
    if not _check_table_exists("inventory_quantities"):
        return {"success": True, "results": [], "row_count": 0,
                "note": "No inventory_quantities table in this database. Inventory tracking is not enabled."}

    COST_ITM = "NULLIF(REGEXP_REPLACE(itm.cost, '[^0-9\\.]', '', 'g'), '')::NUMERIC"
    org_id = params.get("organization_id")
    org_clause = f"WHERE iq.organization_id = '{int(org_id)}'" if org_id else ""
    sql = f"""SELECT itm.name AS item_name, itm.sku,
       iq.quantity_available AS quantity,
       {COST_ITM} AS unit_cost,
       iq.quantity_available * COALESCE({COST_ITM}, 0) AS total_value,
       wh.warehouse_name
FROM inventory_quantities iq
JOIN items itm ON itm.id = iq.item_id
LEFT JOIN warehouses wh ON wh.id = iq.warehouse_id
{org_clause}
ORDER BY total_value DESC NULLS LAST"""
    return _run_sql(sql)


def _task_inventory_movements(params: Dict) -> Dict:
    """Stock in/out events with optional filters."""
    if not _check_table_exists("inventory_movements"):
        return {"success": True, "results": [], "row_count": 0,
                "note": "No inventory_movements table in this database. Inventory tracking is not enabled."}

    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("im", "created_at", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "im", params)

    if params.get("movement_type"):
        clauses.append(f"LOWER(im.movement_type) LIKE '%{params['movement_type'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT im.created_at AS movement_date, itm.name AS item_name,
       im.movement_type, im.quantity, im.unit_cost, im.total_value,
       im.reference_type, im.reference_number,
       wh.warehouse_name
FROM inventory_movements im
JOIN items itm ON itm.id = im.item_id
LEFT JOIN warehouses wh ON wh.id = im.warehouse_id
{where}
ORDER BY im.created_at DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# AR/AP AGING TASKS (NEW)
# ══════════════════════════════════════════════

def _task_ar_aging(params: Dict) -> Dict:
    """
    Accounts Receivable Aging — unpaid invoices grouped into aging buckets based on due_date.
    Uses status_type PENDING or PARTIAL_PAID.
    """
    sql = f"""SELECT COALESCE(c.name, 'Unknown') AS customer,
       inc.invoice_number, inc.invoice_date, inc.due_date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ii.line_amount), 0) AS amount,
       CURRENT_DATE - CAST(inc.due_date AS DATE) AS days_overdue,
       CASE
           WHEN CURRENT_DATE - CAST(inc.due_date AS DATE) <= 0 THEN 'Current'
           WHEN CURRENT_DATE - CAST(inc.due_date AS DATE) BETWEEN 1 AND 30 THEN '1-30 days'
           WHEN CURRENT_DATE - CAST(inc.due_date AS DATE) BETWEEN 31 AND 60 THEN '31-60 days'
           WHEN CURRENT_DATE - CAST(inc.due_date AS DATE) BETWEEN 61 AND 90 THEN '61-90 days'
           WHEN CURRENT_DATE - CAST(inc.due_date AS DATE) BETWEEN 91 AND 120 THEN '91-120 days'
           ELSE '120+ days'
       END AS aging_bucket
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE LOWER(COALESCE(st.value, '')) IN ('pending', 'partial_paid', 'partially_paid'){_org_frag("inc", params)}
GROUP BY c.name, inc.invoice_number, inc.invoice_date, inc.due_date, st.value
ORDER BY days_overdue DESC"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        buckets = {}
        for r in result["results"]:
            b = r.get("aging_bucket", "Unknown")
            if b not in buckets:
                buckets[b] = {"count": 0, "total": 0}
            buckets[b]["count"] += 1
            buckets[b]["total"] += r.get("amount", 0) or 0
        result["summary"] = {
            "aging_buckets": buckets,
            "total_receivable": sum(b["total"] for b in buckets.values()),
            "total_invoices": sum(b["count"] for b in buckets.values()),
        }
    return result


def _task_ap_aging(params: Dict) -> Dict:
    """
    Accounts Payable Aging — unpaid bills grouped into aging buckets based on reception_date.
    Note: expense table has no due_date column; aging is based on reception_date.
    """
    sql = f"""SELECT COALESCE(c.name, 'Unknown') AS vendor,
       e.receipt_number, e.reception_date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ei.line_amount), 0) AS amount,
       CURRENT_DATE - CAST(e.reception_date AS DATE) AS days_outstanding,
       CASE
           WHEN CURRENT_DATE - CAST(e.reception_date AS DATE) <= 30 THEN '0-30 days'
           WHEN CURRENT_DATE - CAST(e.reception_date AS DATE) BETWEEN 31 AND 60 THEN '31-60 days'
           WHEN CURRENT_DATE - CAST(e.reception_date AS DATE) BETWEEN 61 AND 90 THEN '61-90 days'
           WHEN CURRENT_DATE - CAST(e.reception_date AS DATE) BETWEEN 91 AND 120 THEN '91-120 days'
           ELSE '120+ days'
       END AS aging_bucket
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
LEFT JOIN status_type st ON st.id = e.status_type_id
WHERE e.reception_date IS NOT NULL AND e.reception_date <> ''
  AND LOWER(COALESCE(st.value, '')) IN ('pending', 'partial_paid', 'partially_paid'){_org_frag("e", params)}
GROUP BY c.name, e.receipt_number, e.reception_date, st.value
ORDER BY days_outstanding DESC"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        buckets = {}
        for r in result["results"]:
            b = r.get("aging_bucket", "Unknown")
            if b not in buckets:
                buckets[b] = {"count": 0, "total": 0}
            buckets[b]["count"] += 1
            buckets[b]["total"] += r.get("amount", 0) or 0
        result["summary"] = {
            "aging_buckets": buckets,
            "total_payable": sum(b["total"] for b in buckets.values()),
            "total_bills": sum(b["count"] for b in buckets.values()),
        }
    return result


def _task_overdue_invoices(params: Dict) -> Dict:
    """
    Overdue invoices — invoices where due_date < CURRENT_DATE and status is PENDING.
    Does NOT rely on status_type = 'OVERDUE' which may not exist in the DB.
    """
    limit = _resolve_limit(params, default=100, ceiling=500)
    order = order_sql(_direction(params))
    customer_filter = ""
    if params.get("customer"):
        customer_filter = f"AND LOWER(c.name) LIKE '%{params['customer'].lower()}%'"

    sql = f"""SELECT COALESCE(c.name, 'Unknown') AS customer,
       inc.invoice_number, inc.invoice_date, inc.due_date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ii.line_amount), 0) AS amount,
       CURRENT_DATE - CAST(inc.due_date AS DATE) AS days_overdue
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE CAST(inc.due_date AS DATE) < CURRENT_DATE
  AND LOWER(COALESCE(st.value, '')) IN ('pending', 'partial_paid', 'partially_paid'){_org_frag("inc", params)}
  {customer_filter}
GROUP BY c.name, inc.invoice_number, inc.invoice_date, inc.due_date, st.value
ORDER BY days_overdue {order}
LIMIT {int(limit)}"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        total = sum(r.get("amount", 0) or 0 for r in result["results"])
        result["summary"] = {
            "total_overdue_count": len(result["results"]),
            "total_overdue_amount": total,
            "most_overdue_days": max(r.get("days_overdue", 0) or 0 for r in result["results"]) if result["results"] else 0,
        }
    return result


def _task_overdue_bills(params: Dict) -> Dict:
    """
    Overdue bills — expense bills with PENDING/PARTIAL_PAID status older than 30 days.
    Note: expense table has no due_date; uses reception_date > 30 days as overdue proxy.
    """
    limit = _resolve_limit(params, default=100, ceiling=500)
    order = order_sql(_direction(params))
    vendor_filter = ""
    if params.get("vendor"):
        vendor_filter = f"AND LOWER(c.name) LIKE '%{params['vendor'].lower()}%'"

    sql = f"""SELECT COALESCE(c.name, 'Unknown') AS vendor,
       e.receipt_number, e.reception_date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ei.line_amount), 0) AS amount,
       CURRENT_DATE - CAST(e.reception_date AS DATE) AS days_outstanding
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
LEFT JOIN status_type st ON st.id = e.status_type_id
WHERE CAST(e.reception_date AS DATE) < CURRENT_DATE - INTERVAL '30 days'
  AND LOWER(COALESCE(st.value, '')) IN ('pending', 'partial_paid', 'partially_paid'){_org_frag("e", params)}
  {vendor_filter}
GROUP BY c.name, e.receipt_number, e.reception_date, st.value
ORDER BY days_outstanding {order}
LIMIT {int(limit)}"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        total = sum(r.get("amount", 0) or 0 for r in result["results"])
        result["summary"] = {
            "total_overdue_count": len(result["results"]),
            "total_overdue_amount": total,
            "most_overdue_days": max(r.get("days_outstanding", 0) or 0 for r in result["results"]) if result["results"] else 0,
        }
    return result


# ══════════════════════════════════════════════
# AUDIT TASKS (NEW)
# ══════════════════════════════════════════════

def _task_audit_trail(params: Dict) -> Dict:
    """
    Audit trail: who changed what, when. Field-level changes.
    Table: audit_trails — columns: transaction_type, transaction_id, action_type,
    user_name, old_values (JSON), new_values (JSON), changed_fields (JSON)
    """
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("atr", "created_at", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "atr", params)

    if params.get("entity_type") or params.get("transaction_type"):
        et = params.get("entity_type") or params.get("transaction_type")
        clauses.append(f"LOWER(CAST(atr.transaction_type AS TEXT)) LIKE '%{et.lower()}%'")
    if params.get("entity_id") or params.get("transaction_id"):
        eid = params.get("entity_id") or params.get("transaction_id")
        clauses.append(f"atr.transaction_id = {int(eid)}")
    if params.get("action") or params.get("action_type"):
        act = params.get("action") or params.get("action_type")
        clauses.append(f"LOWER(CAST(atr.action_type AS TEXT)) LIKE '%{act.lower()}%'")
    if params.get("user") or params.get("user_name"):
        u = params.get("user") or params.get("user_name")
        clauses.append(f"LOWER(atr.user_name) LIKE '%{u.lower()}%'")
    if params.get("transaction_number"):
        clauses.append(f"LOWER(atr.transaction_number) LIKE '%{params['transaction_number'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT atr.transaction_type, atr.transaction_id, atr.transaction_number,
       atr.action_type, atr.user_name,
       atr.account_name, atr.description,
       atr.amount, atr.debit_amount, atr.credit_amount,
       atr.old_values::TEXT AS old_values, atr.new_values::TEXT AS new_values,
       atr.changed_fields::TEXT AS changed_fields,
       atr.contact_name, atr.status,
       atr.created_at
FROM audit_trails atr
{where}
ORDER BY atr.created_at DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_audit_activity(params: Dict) -> Dict:
    """
    API activity log — technical audit of who accessed what endpoints.
    Table: audit_logs — columns: http_method, url, user_id, user_name, entity_type, action_type
    """
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("al", "created_at", filters)
    clauses.extend(date_clauses)

    if params.get("method") or params.get("http_method"):
        m = params.get("method") or params.get("http_method")
        clauses.append(f"UPPER(al.http_method) = '{m.upper()}'")
    if params.get("url"):
        clauses.append(f"al.url LIKE '%{params['url']}%'")
    if params.get("user_id"):
        clauses.append(f"al.user_id = {int(params['user_id'])}")
    if params.get("entity_type"):
        clauses.append(f"LOWER(CAST(al.entity_type AS TEXT)) LIKE '%{params['entity_type'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT al.http_method, al.url, al.user_id, al.user_name,
       al.action_type, al.entity_type, al.entity_name,
       al.ip_address, al.is_successful, al.severity,
       al.created_at
FROM audit_logs al
{where}
ORDER BY al.created_at DESC
LIMIT {int(limit)}"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# ADVANCED TASKS (NEW)
# ══════════════════════════════════════════════

def _task_project_profitability(params: Dict) -> Dict:
    """
    Project profitability — revenue minus cost. Since journal_entries
    doesn't have a direct project_id FK, we use income/expense with project links
    if available, or list projects with their status.
    """
    clauses = []
    _maybe_org(clauses, "prj", params)
    if params.get("project_name"):
        clauses.append(f"LOWER(prj.project_name) LIKE '%{params['project_name'].lower()}%'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    # List projects
    sql = f"""SELECT prj.id, prj.project_name, prj.is_active, prj.created_at
FROM projects prj
{where}
ORDER BY prj.project_name"""
    return _run_sql(sql)


def _task_expense_by_category(params: Dict) -> Dict:
    """Expenses grouped by expense_category_type, top (or bottom) N by amount."""
    limit = _resolve_limit(params, default=50, ceiling=50)
    order = order_sql(_direction(params))
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "e", params)
    date_clauses = _build_date_filter("e", "reception_date", filters)
    clauses.extend(date_clauses)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT COALESCE(ect.value, 'Uncategorized') AS category,
       COUNT(DISTINCT e.id) AS bill_count,
       COALESCE(SUM(ei.line_amount), 0) AS total_amount
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN expense_category_type ect ON ect.id = e.expense_category_type_id
{where}
GROUP BY ect.value
ORDER BY total_amount {order}
LIMIT {int(limit)}"""
    return _run_sql(sql)


def _task_cost_center_breakdown(params: Dict) -> Dict:
    """Cost centers in the system with their status."""
    org_id = params.get("organization_id")
    org_clause = f"WHERE cc.organization_id = '{int(org_id)}'" if org_id else ""
    sql = f"""SELECT cc.id, cc.costcenter_name, cc.is_active, cc.created_at
FROM cost_centers cc
{org_clause}
ORDER BY cc.costcenter_name"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# PAYMENTS TASKS (NEW)
# ══════════════════════════════════════════════

def _task_customer_payments(params: Dict) -> Dict:
    """List customer payment receipts."""
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("cp", "date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "cp", params)

    if params.get("customer"):
        clauses.append(f"LOWER(c.name) LIKE '%{params['customer'].lower()}%'")
    if params.get("status"):
        clauses.append(f"LOWER(cp.status) = '{params['status'].lower()}'")
    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(cp.date AS DATE)) = {_safe_year(filters['year'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT cp.payment_number, cp.date, cp.amount, cp.currency,
       cp.status, cp.reference,
       COALESCE(c.name, 'Unknown') AS customer,
       pt.value AS payment_method
FROM customer_payment cp
LEFT JOIN contacts c ON c.id = cp.customer_id
LEFT JOIN payment_type pt ON pt.id = cp.payment_type_id
{where}
ORDER BY cp.date DESC
LIMIT {int(limit)}"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["summary"] = {
            "count": len(result["results"]),
            "total": sum(r.get("amount", 0) or 0 for r in result["results"]),
        }
    return result


def _task_vendor_payments(params: Dict) -> Dict:
    """List vendor/supplier expense payments — uses expense table since supplier_payments may not exist."""
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "e", params)

    date_clauses = _build_date_filter("e", "reception_date", filters)
    clauses.extend(date_clauses)

    if params.get("vendor"):
        clauses.append(f"LOWER(c.name) LIKE '%{params['vendor'].lower()}%'")
    if filters.get("status"):
        clauses.append(f"LOWER(st.value) = '{filters['status'].lower()}'")
    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(e.reception_date AS DATE)) = {_safe_year(filters['year'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = params.get("limit", 100)

    sql = f"""SELECT e.receipt_number AS reference, e.reception_date AS date,
       COALESCE(SUM(ei.line_amount), 0) AS amount,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(c.name, 'Unknown') AS vendor,
       COALESCE(ect.value, 'EXPENSE') AS category
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
LEFT JOIN status_type st ON st.id = e.status_type_id
LEFT JOIN expense_category_type ect ON ect.id = e.expense_category_type_id
{where}
GROUP BY e.receipt_number, e.reception_date, st.value, c.name, ect.value
ORDER BY e.reception_date DESC
LIMIT {int(limit)}"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["summary"] = {
            "count": len(result["results"]),
            "total": sum(r.get("amount", 0) or 0 for r in result["results"]),
        }
    return result


def _task_unallocated_payments(params: Dict) -> Dict:
    """Customer payments not fully applied to invoices (excess/unused payments)."""
    entity = params.get("entity", "customer")
    limit = _resolve_limit(params, default=100, ceiling=500)
    order = order_sql(_direction(params))

    if entity in ("vendor", "supplier"):
        sql = f"""SELECT sp.payment_number, sp.date, sp.amount,
           sp.amount - COALESCE(
               (SELECT SUM(spi.amount_applied) FROM supplier_payment_items spi WHERE spi.payment_id = sp.id), 0
           ) AS unallocated_amount,
           COALESCE(c.name, 'Unknown') AS vendor
FROM supplier_payments sp
LEFT JOIN contacts c ON c.id = sp.supplier_id
WHERE sp.amount > COALESCE(
    (SELECT SUM(spi.amount_applied) FROM supplier_payment_items spi WHERE spi.payment_id = sp.id), 0
){_org_frag("sp", params)}
ORDER BY unallocated_amount {order}
LIMIT {int(limit)}"""
    else:
        sql = f"""SELECT cp.payment_number, cp.date, cp.amount,
           cp.amount - COALESCE(
               (SELECT SUM(cpi.amount_applied) FROM customer_payment_items cpi WHERE cpi.payment_id = cp.id), 0
           ) AS unallocated_amount,
           COALESCE(c.name, 'Unknown') AS customer
FROM customer_payment cp
LEFT JOIN contacts c ON c.id = cp.customer_id
WHERE cp.amount > COALESCE(
    (SELECT SUM(cpi.amount_applied) FROM customer_payment_items cpi WHERE cpi.payment_id = cp.id), 0
){_org_frag("cp", params)}
ORDER BY unallocated_amount {order}
LIMIT {int(limit)}"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["summary"] = {
            "count": len(result["results"]),
            "total_unallocated": sum(r.get("unallocated_amount", 0) or 0 for r in result["results"]),
        }
    return result


def _task_payment_forecast(params: Dict) -> Dict:
    """Upcoming invoices/bills — payment forecast for AR and AP."""
    days = params.get("days", 30)
    if isinstance(days, list):
        days = max(days)  # e.g. [7, 15, 30] → use longest horizon
    days = int(days)
    entity = params.get("entity", "both")
    limit = params.get("limit", 50)

    results_combined = []

    if entity in ("both", "customer", "invoice", "ar"):
        # Invoices with due_date within the next N days (or recently overdue within last 30 days)
        sql = f"""SELECT 'Invoice' AS type, inc.invoice_number AS reference,
           COALESCE(c.name, 'Unknown') AS contact,
           inc.due_date, inc.invoice_date,
           COALESCE(st.value, 'Unknown') AS status,
           CAST(inc.due_date AS DATE) - CURRENT_DATE AS days_until_due,
           COALESCE(SUM(ii.line_amount), 0) AS amount
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE COALESCE(st.value, '') NOT IN ('PAID', 'CANCELLED', 'VOIDED')
  AND inc.due_date IS NOT NULL AND inc.due_date <> ''
  AND CAST(inc.due_date AS DATE) BETWEEN CURRENT_DATE - INTERVAL '30 days' AND CURRENT_DATE + INTERVAL '{days} days'{_org_frag("inc", params)}
GROUP BY inc.invoice_number, c.name, inc.due_date, inc.invoice_date, st.value
ORDER BY inc.due_date
LIMIT {int(limit)}"""
        r = _run_sql(sql)
        if r["success"]:
            results_combined.extend(r["results"])

    if entity in ("both", "vendor", "bill", "ap"):
        # Bills: use reception_date from last 2 years that are still pending
        # (expense has no due_date; recent pending bills = likely unpaid)
        sql = f"""SELECT 'Bill' AS type, e.receipt_number AS reference,
           COALESCE(c.name, 'Unknown') AS contact,
           e.reception_date AS due_date,
           e.reception_date AS bill_date,
           COALESCE(st.value, 'Unknown') AS status,
           CURRENT_DATE - CAST(e.reception_date AS DATE) AS days_outstanding,
           COALESCE(SUM(ei.line_amount), 0) AS amount
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
LEFT JOIN status_type st ON st.id = e.status_type_id
WHERE COALESCE(st.value, '') NOT IN ('PAID', 'CANCELLED', 'VOIDED')
  AND e.reception_date IS NOT NULL AND e.reception_date <> ''
  AND CAST(e.reception_date AS DATE) >= CURRENT_DATE - INTERVAL '2 years'{_org_frag("e", params)}
GROUP BY e.receipt_number, c.name, e.reception_date, st.value
ORDER BY e.reception_date DESC
LIMIT {int(limit)}"""
        r = _run_sql(sql)
        if r["success"]:
            results_combined.extend(r["results"])

    return {
        "success": True,
        "results": results_combined,
        "row_count": len(results_combined),
        "days": days,
        "summary": {
            "total_items": len(results_combined),
            "invoices": sum(1 for r in results_combined if r.get("type") == "Invoice"),
            "bills": sum(1 for r in results_combined if r.get("type") == "Bill"),
            "total_ar": sum(r.get("amount", 0) or 0 for r in results_combined if r.get("type") == "Invoice"),
            "total_ap": sum(r.get("amount", 0) or 0 for r in results_combined if r.get("type") == "Bill"),
        }
    }


# ══════════════════════════════════════════════
# RECONCILIATION & STRUCTURE TASKS (NEW)
# ══════════════════════════════════════════════

def _task_reconciliation_status(params: Dict) -> Dict:
    """Bank reconciliation status from reconciliations table."""
    limit = params.get("limit", 100)
    sql = f"""SELECT r.id, ba.account_name, ba.bank_name,
       r.start_date, r.end_date, r.date,
       r.status, r.status_formatted,
       r.closing_balances, r.opening_balances,
       r.is_last_reconcile
FROM reconciliations r
LEFT JOIN bank_accounts ba ON CAST(ba.id AS TEXT) = r.account_id
ORDER BY r.date DESC
LIMIT {int(limit)}"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        by_status = {}
        for r in result["results"]:
            s = r.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        result["summary"] = {
            "total_reconciliations": len(result["results"]),
            "by_status": by_status,
        }
    return result


def _task_branch_summary(params: Dict) -> Dict:
    """List branches for the organization."""
    org_id = params.get("organization_id")
    org_clause = f"WHERE b.organization_id = '{int(org_id)}'" if org_id else ""
    sql = f"""SELECT b.id, b.branch_name, b.display_name,
       b.phone, b.city, b.district,
       b.is_active, b.created_at
FROM branches b
{org_clause}
ORDER BY b.branch_name"""
    return _run_sql(sql)


def _task_cash_flow_summary(params: Dict) -> Dict:
    """Categorized bank cash inflow/outflow summary from bank_transactions."""
    filters = params.get("filters", {})
    clauses = []
    date_clauses = _build_date_filter("bt", "date", filters)
    clauses.extend(date_clauses)
    _maybe_org(clauses, "bt", params)

    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM bt.date) = {_safe_year(filters['year'])}")
    if filters.get("month"):
        clauses.append(f"EXTRACT(MONTH FROM bt.date) = {_safe_month(filters['month'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT bt.debit_or_credit,
       COALESCE(bt.category, 'Uncategorized') AS category,
       COUNT(*) AS transaction_count,
       COALESCE(SUM(bt.amount), 0) AS total_amount
FROM bank_transactions bt
{where}
GROUP BY bt.debit_or_credit, bt.category
ORDER BY bt.debit_or_credit, total_amount DESC"""

    result = _run_sql(sql)
    if result["success"] and result["results"]:
        inflow = sum(r["total_amount"] for r in result["results"] if (r.get("debit_or_credit") or "").upper() == "CREDIT")
        outflow = sum(r["total_amount"] for r in result["results"] if (r.get("debit_or_credit") or "").upper() == "DEBIT")
        result["summary"] = {
            "total_inflow": inflow,
            "total_outflow": outflow,
            "net_cash_flow": inflow - outflow,
        }
    return result


def _task_invoice_status_summary(params: Dict) -> Dict:
    """Invoice count and total amount broken down by status."""
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "inc", params)
    date_clauses = _build_date_filter("inc", "invoice_date", filters)
    clauses.extend(date_clauses)

    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(inc.invoice_date AS DATE)) = {_safe_year(filters['year'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT COALESCE(st.value, 'Unknown') AS status,
       COUNT(DISTINCT inc.id) AS invoice_count,
       COALESCE(SUM(ii.line_amount), 0) AS total_amount
FROM income inc
LEFT JOIN status_type st ON st.id = inc.status_type_id
LEFT JOIN income_items ii ON ii.income_id = inc.id
{where}
GROUP BY st.value
ORDER BY total_amount DESC"""
    return _run_sql(sql)


def _task_bill_status_summary(params: Dict) -> Dict:
    """Bill count and total amount broken down by status."""
    filters = params.get("filters", {})
    clauses = []
    _maybe_org(clauses, "e", params)
    date_clauses = _build_date_filter("e", "reception_date", filters)
    clauses.extend(date_clauses)

    if filters.get("year"):
        clauses.append(f"EXTRACT(YEAR FROM CAST(e.reception_date AS DATE)) = {_safe_year(filters['year'])}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""SELECT COALESCE(st.value, 'Unknown') AS status,
       COUNT(DISTINCT e.id) AS bill_count,
       COALESCE(SUM(ei.line_amount), 0) AS total_amount
FROM expense e
LEFT JOIN status_type st ON st.id = e.status_type_id
LEFT JOIN expense_items ei ON ei.expense_id = e.id
{where}
GROUP BY st.value
ORDER BY total_amount DESC"""
    return _run_sql(sql)


# ══════════════════════════════════════════════
# ADDITIONAL TASKS for common question patterns
# ══════════════════════════════════════════════

def _task_weekly_transaction_summary(params: Dict) -> Dict:
    """
    Summary of this week's financial activity: invoices issued, bills received, payments.
    Covers the last 7 days from today.
    """
    sql = f"""SELECT 'invoice' AS type,
       COALESCE(st.value, 'Unknown') AS status,
       COUNT(DISTINCT inc.id) AS count,
       COALESCE(SUM(ii.line_amount), 0) AS total_amount
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE CAST(inc.invoice_date AS DATE) >= CURRENT_DATE - INTERVAL '7 days'{_org_frag("inc", params)}
GROUP BY st.value
UNION ALL
SELECT 'bill' AS type,
       COALESCE(st.value, 'Unknown') AS status,
       COUNT(DISTINCT e.id) AS count,
       COALESCE(SUM(ei.line_amount), 0) AS total_amount
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN status_type st ON st.id = e.status_type_id
WHERE CAST(e.reception_date AS DATE) >= CURRENT_DATE - INTERVAL '7 days'{_org_frag("e", params)}
GROUP BY st.value
ORDER BY type, total_amount DESC"""
    result = _run_sql(sql)
    if result["success"]:
        invoices = [r for r in result["results"] if r.get("type") == "invoice"]
        bills = [r for r in result["results"] if r.get("type") == "bill"]
        result["summary"] = {
            "invoice_total": sum(r.get("total_amount", 0) or 0 for r in invoices),
            "invoice_count": sum(r.get("count", 0) or 0 for r in invoices),
            "bill_total": sum(r.get("total_amount", 0) or 0 for r in bills),
            "bill_count": sum(r.get("count", 0) or 0 for r in bills),
        }
    return result


def _task_monthly_revenue_trend(params: Dict) -> Dict:
    """Monthly revenue trend for the last N months from journal entries."""
    months = params.get("months", 12)
    sql = f"""SELECT
    DATE_TRUNC('month', je.transaction_date)::DATE AS month,
    COALESCE(SUM(CASE WHEN LOWER(COALESCE(coa.account_type,'')) IN ('revenue','income') THEN jel.credit_amount - jel.debit_amount ELSE 0 END), 0) AS revenue,
    COALESCE(SUM(CASE WHEN LOWER(COALESCE(coa.account_type,'')) IN ('expense','cost of goods sold','operating expense') THEN jel.debit_amount - jel.credit_amount ELSE 0 END), 0) AS expenses
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
WHERE je.transaction_date >= CURRENT_DATE - INTERVAL '{int(months)} months'
  AND LOWER(COALESCE(coa.account_type,'')) IN ('revenue','income','expense','cost of goods sold','operating expense'){_org_frag("je", params)}
GROUP BY DATE_TRUNC('month', je.transaction_date)
ORDER BY month DESC"""
    return _run_sql(sql)


def _task_vat_summary(params: Dict) -> Dict:
    """
    VAT summary: output VAT owed and input VAT claimable based on pending invoices/bills.
    For UAE: 5% VAT rate.
    Uses journal entries to find VAT account balances.
    """
    filters = params.get("filters", {})
    limit = _resolve_limit(params, default=20, ceiling=50)
    order = order_sql(_direction(params))
    # First try journal entries for VAT accounts
    date_clauses = _build_date_filter("je", "transaction_date", filters)
    where = f"WHERE {' AND '.join(date_clauses)}" if date_clauses else ""

    sql = f"""SELECT jel.account_name,
       COALESCE(coa.account_type, 'Unknown') AS account_type,
       COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0) AS net_balance
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
{where}
WHERE (LOWER(jel.account_name) LIKE '%vat%'
    OR LOWER(jel.account_name) LIKE '%tax%'
    OR LOWER(jel.account_name) LIKE '%value added%')
GROUP BY jel.account_name, coa.account_type
ORDER BY ABS(COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0)) {order}
LIMIT {int(limit)}"""
    # Fix the WHERE duplication
    if date_clauses:
        sql = f"""SELECT jel.account_name,
       COALESCE(coa.account_type, 'Unknown') AS account_type,
       COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0) AS net_balance
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
WHERE {' AND '.join(date_clauses)}
  AND (LOWER(jel.account_name) LIKE '%vat%'
    OR LOWER(jel.account_name) LIKE '%tax%'
    OR LOWER(jel.account_name) LIKE '%value added%'){_org_frag("je", params)}
GROUP BY jel.account_name, coa.account_type
ORDER BY ABS(COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0)) {order}
LIMIT {int(limit)}"""
    else:
        sql = f"""SELECT jel.account_name,
       COALESCE(coa.account_type, 'Unknown') AS account_type,
       COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0) AS net_balance
FROM journal_entry_lines jel
JOIN journal_entries je ON je.id = jel.journal_entry_id
LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id
WHERE (LOWER(jel.account_name) LIKE '%vat%'
   OR LOWER(jel.account_name) LIKE '%tax%'
   OR LOWER(jel.account_name) LIKE '%value added%'){_org_frag("je", params)}
GROUP BY jel.account_name, coa.account_type
ORDER BY ABS(COALESCE(SUM(jel.credit_amount), 0) - COALESCE(SUM(jel.debit_amount), 0)) {order}
LIMIT {int(limit)}"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        output_vat = sum(r.get("net_balance", 0) or 0
                        for r in result["results"]
                        if "output" in (r.get("account_name") or "").lower()
                        or ("vat" in (r.get("account_name") or "").lower()
                            and r.get("net_balance", 0) > 0))
        input_vat = abs(sum(r.get("net_balance", 0) or 0
                           for r in result["results"]
                           if "input" in (r.get("account_name") or "").lower()
                           or ("vat" in (r.get("account_name") or "").lower()
                               and r.get("net_balance", 0) < 0)))
        result["summary"] = {
            "output_vat_payable": output_vat,
            "input_vat_claimable": input_vat,
            "net_vat_payable": output_vat - input_vat,
            "jurisdiction": "UAE (5% VAT)",
        }
    return result


def _task_recent_transactions(params: Dict) -> Dict:
    """
    Recent transactions (last N days) across invoices, bills, and bank transactions.
    """
    days = params.get("days", 30)
    limit = params.get("limit", 50)
    entity = params.get("entity", "all")

    results = []

    if entity in ("all", "invoice"):
        sql = f"""SELECT 'Invoice' AS type, inc.invoice_number AS reference,
       COALESCE(c.name, 'Unknown') AS contact,
       inc.invoice_date AS date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ii.line_amount), 0) AS amount
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE CAST(inc.invoice_date AS DATE) >= CURRENT_DATE - INTERVAL '{int(days)} days'{_org_frag("inc", params)}
GROUP BY inc.invoice_number, c.name, inc.invoice_date, st.value
ORDER BY inc.invoice_date DESC
LIMIT {int(limit)}"""
        r = _run_sql(sql)
        if r["success"]:
            results.extend(r["results"])

    if entity in ("all", "bill"):
        sql = f"""SELECT 'Bill' AS type, e.receipt_number AS reference,
       COALESCE(c.name, 'Unknown') AS contact,
       e.reception_date AS date,
       COALESCE(st.value, 'Unknown') AS status,
       COALESCE(SUM(ei.line_amount), 0) AS amount
FROM expense e
JOIN expense_items ei ON ei.expense_id = e.id
LEFT JOIN contacts c ON c.id = e.contact_id
LEFT JOIN status_type st ON st.id = e.status_type_id
WHERE CAST(e.reception_date AS DATE) >= CURRENT_DATE - INTERVAL '{int(days)} days'{_org_frag("e", params)}
GROUP BY e.receipt_number, c.name, e.reception_date, st.value
ORDER BY e.reception_date DESC
LIMIT {int(limit)}"""
        r = _run_sql(sql)
        if r["success"]:
            results.extend(r["results"])

    results.sort(key=lambda x: x.get("date") or "", reverse=True)
    return {
        "success": True,
        "results": results[:int(limit)],
        "row_count": len(results),
        "summary": {
            "total_items": len(results),
            "invoices": sum(1 for r in results if r.get("type") == "Invoice"),
            "bills": sum(1 for r in results if r.get("type") == "Bill"),
        }
    }


def _task_customer_overdue_summary(params: Dict) -> Dict:
    """Top (or bottom, via sort_order) N customers with overdue invoices — amount, count and days overdue."""
    limit = _resolve_limit(params, default=20, ceiling=50)
    order = order_sql(_direction(params))
    min_days = params.get("min_days_overdue", 0)
    sql = f"""SELECT COALESCE(c.name, 'Unknown') AS customer,
       COUNT(DISTINCT inc.id) AS overdue_invoice_count,
       COALESCE(SUM(ii.line_amount), 0) AS total_overdue_amount,
       MAX(CURRENT_DATE - CAST(inc.due_date AS DATE)) AS max_days_overdue,
       MIN(CURRENT_DATE - CAST(inc.due_date AS DATE)) AS min_days_overdue,
       c.email, c.phone_number, c.trn_number
FROM income inc
JOIN income_items ii ON ii.income_id = inc.id
LEFT JOIN contacts c ON c.id = inc.contact_id
LEFT JOIN status_type st ON st.id = inc.status_type_id
WHERE inc.due_date IS NOT NULL AND inc.due_date <> ''
  AND CAST(inc.due_date AS DATE) < CURRENT_DATE
  AND LOWER(COALESCE(st.value, '')) IN ('pending', 'partial_paid')
  AND CURRENT_DATE - CAST(inc.due_date AS DATE) >= {int(min_days)}{_org_frag("inc", params)}
GROUP BY c.name, c.email, c.phone_number, c.trn_number
ORDER BY total_overdue_amount {order} NULLS LAST
LIMIT {int(limit)}"""
    result = _run_sql(sql)
    if result["success"] and result["results"]:
        result["summary"] = {
            "customers_with_overdue": len(result["results"]),
            "total_overdue": sum(r.get("total_overdue_amount", 0) or 0 for r in result["results"]),
        }
    return result

