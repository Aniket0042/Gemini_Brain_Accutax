"""
definitions.py — Deterministic SQL reports, ported from the arena's report_engine.

Each function takes (query_params, org_id, db_name) and returns a JSON-safe dict
shaped like the REST payloads the narrator already handles: a `summary` of the
headline figures plus a named row collection.

The SQL is ported against the accutax_bk schema. Note: contacts/sub_contacts have
is_deleted columns, whereas income/expense/income_items/expense_items use
status_type_id ('CANCELLED', 'VOIDED') and voided_at for voiding/cancellation.

Deliberately NOT ported from the arena:
  - consolidated_pl / consolidated_cash_flow / consolidated_balance_sheet: in a
    single-org setup all three just delegate to profit_loss / cash_flow /
    balance_sheet and relabel the title. The registry already has those, and
    near-duplicate tool descriptions make the router's job harder, not easier.
  - sales_by_contact: an alias of sales_by_customer, which is already registered.
  - vat_report / customer_balance / profit_loss_by_accounts: overlap the existing
    vat_summary, customer_balance_summary and profit_loss_with_accounts tools.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Callable, Dict, List

from gemini_brain.reports.engine import parse_date, query, total_of
from gemini_brain.utils.ranking import order_sql
from gemini_brain.utils.ranking import resolve_direction as _direction
from gemini_brain.utils.ranking import resolve_limit as _resolve_limit

logger = logging.getLogger("gemini_brain.reports.definitions")


def _period(params: Dict[str, Any]) -> tuple[str, str]:
    """Resolve a start/end pair, defaulting to the current calendar year."""
    today = datetime.date.today()
    start = parse_date(params.get("start_date"), datetime.date(today.year, 1, 1))
    end = parse_date(params.get("end_date"), datetime.date(today.year, 12, 31))
    return start, end


def _as_of(params: Dict[str, Any]) -> str:
    return parse_date(params.get("as_of_date") or params.get("as_of"), datetime.date.today())


def _limit(params: Dict[str, Any], default: int = 20, ceiling: int = 50) -> int:
    """Bound a caller-supplied limit — it reaches us from model output."""
    return _resolve_limit(params, default, ceiling)


# ── Aging detail ─────────────────────────────────────────────────────────────

def aged_receivables_detail(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Invoice-by-invoice overdue receivables. The registry's ar_aging gives buckets only."""
    as_of = _as_of(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            inc.id,
            inc.invoice_number,
            COALESCE(c.name, c.organization_name, 'Unknown') AS customer,
            CAST(inc.invoice_date AS DATE)                    AS invoice_date,
            CAST(inc.due_date AS DATE)                        AS due_date,
            (%s::DATE - CAST(inc.due_date AS DATE))           AS days_overdue,
            COALESCE(SUM(ii.line_amount), 0) - inc.amount_paid AS outstanding
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   contacts c      ON c.id = inc.contact_id
        JOIN   status_type st  ON st.id = inc.status_type_id
        WHERE  inc.organization_id = %s
          AND  st.value IN ('PENDING','PARTIALLY_PAID')
          AND  CAST(inc.due_date AS DATE) < %s::DATE
        GROUP  BY inc.id, inc.invoice_number, c.organization_name, c.name,
                  inc.invoice_date, inc.due_date, inc.amount_paid
        ORDER  BY days_overdue {order}
        LIMIT  %s
        """,
        (as_of, org_id, as_of, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Aged Receivables Detail",
        "as_of": as_of,
        "summary": {
            "total_invoices": len(rows),
            "total_outstanding": total_of(rows, "outstanding"),
        },
        "invoices": rows,
    }


def aged_payables_detail(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Bill-by-bill overdue payables."""
    as_of = _as_of(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            e.receipt_number                                  AS bill_number,
            COALESCE(c.name, c.organization_name, 'Unknown')  AS vendor,
            CAST(e.reception_date AS DATE)                    AS bill_date,
            CAST(e.due_date AS DATE)                          AS due_date,
            (%s::DATE - CAST(e.due_date AS DATE))             AS days_overdue,
            COALESCE(SUM(ei.line_amount), 0) - e.amount_paid  AS outstanding
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   contacts c       ON c.id = e.contact_id
        JOIN   status_type st   ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value IN ('PENDING','PARTIALLY_PAID')
          AND  CAST(e.due_date AS DATE) < %s::DATE
        GROUP  BY e.id, e.receipt_number, c.organization_name, c.name,
                  e.reception_date, e.due_date, e.amount_paid
        ORDER  BY days_overdue {order}
        LIMIT  %s
        """,
        (as_of, org_id, as_of, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Aged Payables Detail",
        "as_of": as_of,
        "summary": {
            "total_bills": len(rows),
            "total_outstanding": total_of(rows, "outstanding"),
        },
        "bills": rows,
    }


# ── Contact statements ───────────────────────────────────────────────────────

def bills_by_contact(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Purchase totals, payments and outstanding balance per vendor."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(c.name, c.organization_name, 'Unknown') AS vendor,
            COUNT(DISTINCT e.id)                             AS bill_count,
            COALESCE(SUM(ei.line_amount), 0)                 AS total_amount,
            COALESCE(SUM(e.amount_paid), 0)                  AS total_paid,
            COALESCE(SUM(ei.line_amount), 0)
              - COALESCE(SUM(e.amount_paid), 0)              AS outstanding
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   contacts c       ON c.id = e.contact_id
        JOIN   status_type st   ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
        GROUP  BY c.organization_name, c.name
        ORDER  BY total_amount {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Bills by Contact",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_amount": total_of(rows, "total_amount"),
            "vendor_count": len(rows),
        },
        "vendors": rows,
    }


def expenses_by_contact(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Expense spend grouped by contact."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(c.name, c.organization_name, 'Unknown') AS contact,
            COUNT(DISTINCT e.id)                             AS expense_count,
            COALESCE(SUM(ei.line_amount), 0)                 AS total_amount
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   contacts c       ON c.id = e.contact_id
        JOIN   status_type st   ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
        GROUP  BY c.organization_name, c.name
        ORDER  BY total_amount {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Expenses by Contact",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_expenses": total_of(rows, "total_amount"),
            "contact_count": len(rows),
        },
        "contacts": rows,
    }


def supplier_statement(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Statement of account per supplier: purchased, paid, balance due."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(c.name, c.organization_name, 'Unknown') AS supplier,
            COUNT(DISTINCT e.id)                             AS total_bills,
            COALESCE(SUM(ei.line_amount), 0)                 AS total_purchases,
            COALESCE(SUM(e.amount_paid), 0)                  AS total_paid,
            COALESCE(SUM(ei.line_amount), 0)
              - COALESCE(SUM(e.amount_paid), 0)              AS balance_due
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   contacts c       ON c.id = e.contact_id
        JOIN   status_type st   ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
        GROUP  BY c.organization_name, c.name
        ORDER  BY total_purchases {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Supplier Statement of Account",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_purchases": total_of(rows, "total_purchases"),
            "total_balance_due": total_of(rows, "balance_due"),
            "supplier_count": len(rows),
        },
        "suppliers": rows,
    }


# ── Dimensional P&L ──────────────────────────────────────────────────────────

def profit_loss_by_project(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Revenue segmented by project."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(p.project_name, 'No Project') AS project,
            COALESCE(SUM(ii.line_amount), 0)       AS revenue
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        LEFT   JOIN projects p ON p.id = inc.project_id
        WHERE  inc.organization_id = %s
          AND  UPPER(inc.income_type) = 'INVOICE'
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        GROUP  BY p.project_name
        ORDER  BY revenue {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Profit & Loss by Project",
        "period": {"start_date": start, "end_date": end},
        "summary": {"total_revenue": total_of(rows, "revenue"), "project_count": len(rows)},
        "by_project": rows,
    }


def profit_loss_by_cost_center(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Revenue segmented by cost centre."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(cc.costcenter_name, 'Unallocated') AS cost_center,
            COALESCE(SUM(ii.line_amount), 0)            AS amount
        FROM   income inc
        JOIN   income_items ii  ON ii.income_id = inc.id
        JOIN   status_type  st  ON st.id = inc.status_type_id
        LEFT   JOIN cost_centers cc ON cc.id = ii.cost_center_id
        WHERE  inc.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        GROUP  BY cc.costcenter_name
        ORDER  BY amount {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Profit & Loss by Cost Center",
        "period": {"start_date": start, "end_date": end},
        "summary": {"total_revenue": total_of(rows, "amount"), "cost_center_count": len(rows)},
        "by_cost_center": rows,
    }


def sales_by_project(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Invoice count and revenue per project."""
    start, end = _period(params)
    limit = _limit(params)
    order = order_sql(_direction(params))
    rows = query(
        f"""
        SELECT
            COALESCE(p.project_name, 'No Project') AS project,
            COUNT(DISTINCT inc.id)                 AS invoice_count,
            COALESCE(SUM(ii.line_amount), 0)       AS total_revenue
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        LEFT   JOIN projects p ON p.id = inc.project_id
        WHERE  inc.organization_id = %s
          AND  UPPER(inc.income_type) = 'INVOICE'
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        GROUP  BY p.project_name
        ORDER  BY total_revenue {order}
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": "Sales by Project",
        "period": {"start_date": start, "end_date": end},
        "summary": {"total_revenue": total_of(rows, "total_revenue"), "project_count": len(rows)},
        "projects": rows,
    }


# ── Sales operations ─────────────────────────────────────────────────────────

def estimate_conversion(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """How many quotes turned into business, by status."""
    start, end = _period(params)
    rows = query(
        """
        SELECT
            st.value                         AS status,
            COUNT(*)                         AS count,
            COALESCE(SUM(ii.line_amount), 0) AS total_amount
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        WHERE  inc.organization_id = %s
          AND  UPPER(inc.income_type) = 'ESTIMATE'
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        GROUP  BY st.value
        ORDER  BY count DESC
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    total = sum(int(r.get("count") or 0) for r in rows)
    converted = sum(int(r.get("count") or 0) for r in rows if r.get("status") == "ACCEPTED")
    return {
        "report": "Estimate Conversion Rate",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_estimates": total,
            "converted": converted,
            "conversion_rate_pct": round(converted / total * 100, 2) if total else 0.0,
        },
        "by_status": rows,
    }


# ── VAT detail ───────────────────────────────────────────────────────────────

def vat_input_output(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Output vs input VAT, month by month, with the net payable."""
    start, end = _period(params)
    output_rows = query(
        """
        SELECT
            TO_CHAR(CAST(inc.invoice_date AS DATE), 'YYYY-MM') AS month,
            COALESCE(SUM(ii.tax_amount), 0)                    AS output_vat,
            COALESCE(SUM(ii.line_amount), 0)                   AS taxable_sales
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        WHERE  inc.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
          AND  ii.tax_amount > 0
        GROUP  BY month
        ORDER  BY month
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    input_rows = query(
        """
        SELECT
            TO_CHAR(CAST(e.reception_date AS DATE), 'YYYY-MM') AS month,
            COALESCE(SUM(ei.tax_amount), 0)                    AS input_vat,
            COALESCE(SUM(ei.line_amount), 0)                   AS taxable_purchases
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   status_type   st ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
          AND  ei.tax_amount > 0
        GROUP  BY month
        ORDER  BY month
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    output_vat = total_of(output_rows, "output_vat")
    input_vat = total_of(input_rows, "input_vat")
    return {
        "report": "VAT Input/Output Report",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_output_vat": output_vat,
            "total_input_vat": input_vat,
            "net_vat_payable": round(output_vat - input_vat, 2),
        },
        "output_vat_by_month": output_rows,
        "input_vat_by_month": input_rows,
    }


def vat_export_return(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """The figures an FTA VAT return needs, for one period."""
    start, end = _period(params)
    sales = query(
        """
        SELECT
            COALESCE(SUM(ii.line_amount), 0) AS standard_rated_sales,
            COALESCE(SUM(ii.tax_amount),  0) AS output_vat
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        WHERE  inc.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    purchases = query(
        """
        SELECT
            COALESCE(SUM(ei.line_amount), 0) AS standard_rated_purchases,
            COALESCE(SUM(ei.tax_amount),  0) AS input_vat
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   status_type   st ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    s = sales[0] if sales else {}
    p = purchases[0] if purchases else {}
    output_vat = float(s.get("output_vat") or 0)
    input_vat = float(p.get("input_vat") or 0)
    return {
        "report": "VAT Export Return",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "standard_rated_sales": round(float(s.get("standard_rated_sales") or 0), 2),
            "output_vat": round(output_vat, 2),
            "standard_rated_purchases": round(float(p.get("standard_rated_purchases") or 0), 2),
            "input_vat_recoverable": round(input_vat, 2),
            "net_vat_payable": round(output_vat - input_vat, 2),
            # The schema carries no zero-rated/exempt classification, so these are
            # reported as zero rather than guessed. Stated explicitly because a VAT
            # return with a silently-omitted box is worse than one with a zero in it.
            "zero_rated_sales": 0.0,
            "exempt_sales": 0.0,
        },
    }


# ── Aggregate totals ─────────────────────────────────────────────────────────

def income_total(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Total invoiced revenue for a period, read straight from the subledger.

    Replaces the REST /income/total endpoint, which was found to ignore
    organization_id entirely — it returned the identical figure for every
    org tested, including orgs that do not exist. Confirmed the REST backend
    and this database are separate systems with no overlap for these tenants,
    so there is no REST fix available; this report reads the same tables
    finance_agent's get_invoice_total task already reads, directly.
    """
    start, end = _period(params)
    rows = query(
        """
        SELECT COALESCE(SUM(ii.line_amount), 0) AS total_income,
               COUNT(DISTINCT inc.id)            AS invoice_count
        FROM   income inc
        JOIN   income_items ii ON ii.income_id = inc.id
        JOIN   status_type  st ON st.id = inc.status_type_id
        WHERE  inc.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    row = rows[0] if rows else {}
    return {
        "report": "Total Income",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_income": round(float(row.get("total_income") or 0), 2),
            "invoice_count": int(row.get("invoice_count") or 0),
        },
    }


# ── Invoice list ─────────────────────────────────────────────────────────────

#: Status words a question or the fast router may use, mapped to status_type values.
_INVOICE_STATUS_GROUPS: Dict[str, tuple] = {
    "unpaid": ("PENDING", "PARTIALLY_PAID"),
    "paid": ("PAID",),
}


def _invoice_statuses(params: Dict[str, Any]) -> tuple:
    """status_type values to keep; () keeps every status."""
    raw = str(params.get("status") or "").strip()
    if not raw or raw.lower() == "all":
        return ()
    values: List[str] = []
    for part in raw.split(","):
        key = part.strip()
        values.extend(_INVOICE_STATUS_GROUPS.get(key.lower(), (key.upper(),)))
    return tuple(dict.fromkeys(v for v in values if v))


def invoice_list(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Sales invoices of one organization for a period, newest first.

    Org-scoped stand-in for REST /income/list, which filters by user_id only
    and so returns every organization the user belongs to. Columns mirror what
    /income/list returns. Totals cover the whole period, not only listed rows.
    """
    start, end = _period(params)
    limit = _limit(params, default=50, ceiling=200)
    statuses = _invoice_statuses(params)
    status_sql = "AND st.value IN %s" if statuses else ""
    status_args: tuple = (statuses,) if statuses else ()

    per_invoice = f"""
        SELECT inc.id,
               inc.invoice_number,
               COALESCE(c.name, c.organization_name, 'Unknown')      AS customer,
               CAST(inc.invoice_date AS DATE)                         AS invoice_date,
               CAST(inc.due_date AS DATE)                             AS due_date,
               st.value                                               AS status,
               COALESCE(SUM(CAST(ii.line_amount AS DECIMAL)
                            + COALESCE(CAST(ii.tax_amount AS DECIMAL), 0)), 0) AS total,
               COALESCE(inc.amount_paid, 0)                           AS amount_paid
        FROM   income inc
        JOIN   income_items ii     ON ii.income_id = inc.id
        JOIN   status_type st      ON st.id = inc.status_type_id
        LEFT   JOIN contacts c     ON c.id = inc.contact_id
        WHERE  inc.organization_id = %s
          AND  CAST(inc.invoice_date AS DATE) BETWEEN %s AND %s
          {status_sql}
        GROUP  BY inc.id, inc.invoice_number, c.name, c.organization_name,
                  inc.invoice_date, inc.due_date, st.value, inc.amount_paid
    """
    base_args = (org_id, start, end, *status_args)

    # Only open invoices carry a balance. Payment state lives in the status:
    # amount_paid stays 0 even on PAID invoices, so it cannot be trusted alone.
    balance_sql = (
        "CASE WHEN status IN ('PENDING','PARTIALLY_PAID') "
        "THEN total - amount_paid ELSE 0 END"
    )
    rows = query(
        f"""
        SELECT *, {balance_sql} AS balance
        FROM   ({per_invoice}) t
        ORDER  BY invoice_date DESC, id DESC
        LIMIT  %s
        """,
        (*base_args, limit),
        org_id,
        db_name,
    )
    totals = query(
        f"""
        SELECT COUNT(*)                            AS invoice_count,
               COALESCE(SUM(total), 0)             AS total_invoiced,
               COALESCE(SUM({balance_sql}), 0)     AS total_outstanding
        FROM   ({per_invoice}) t
        """,
        base_args,
        org_id,
        db_name,
    )
    agg = totals[0] if totals else {}
    return {
        "report": "Invoice List",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "invoice_count": int(agg.get("invoice_count") or 0),
            "total_invoiced": round(float(agg.get("total_invoiced") or 0), 2),
            "total_outstanding": round(float(agg.get("total_outstanding") or 0), 2),
            "listed": len(rows),
        },
        "invoices": rows,
    }


# ── Comparable metrics (multi-org) ───────────────────────────────────────────
# One organization's headline figure each, in `summary`. The multi-org
# comparison reads these fields directly, so their names are a contract with
# orchestrator/multi_org_metrics.py.

def expense_total(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Total expenses (bills) for a period, excluding cancelled and voided ones."""
    start, end = _period(params)
    rows = query(
        """
        SELECT COALESCE(SUM(ei.line_amount), 0) AS total_expenses,
               COUNT(DISTINCT e.id)             AS bill_count
        FROM   expense e
        JOIN   expense_items ei ON ei.expense_id = e.id
        JOIN   status_type  st  ON st.id = e.status_type_id
        WHERE  e.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST(e.reception_date AS DATE) BETWEEN %s AND %s
        """,
        (org_id, start, end),
        org_id,
        db_name,
    )
    row = rows[0] if rows else {}
    return {
        "report": "Total Expenses",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_expenses": round(float(row.get("total_expenses") or 0), 2),
            "bill_count": int(row.get("bill_count") or 0),
        },
    }


def profit_summary(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Income, expenses and their difference for a period (same rules as the two totals)."""
    income = income_total(params, org_id, db_name)["summary"]["total_income"]
    expenses = expense_total(params, org_id, db_name)["summary"]["total_expenses"]
    start, end = _period(params)
    net = round(income - expenses, 2)
    return {
        "report": "Profit Summary",
        "period": {"start_date": start, "end_date": end},
        "summary": {
            "total_income": income,
            "total_expenses": expenses,
            "net_profit": net,
            # Net profit as a share of income; undefined without income.
            "profit_margin_pct": round(100.0 * net / income, 2) if income else None,
        },
    }


# ── Growth, series and balance sheet (multi-org metrics) ─────────────────────

def _period_value(base: str, start: str, end: str, org_id: int, db_name: str) -> float:
    """Income, expenses or profit for one window, with the same rules as the totals."""
    window = {"start_date": start, "end_date": end}
    if base == "income":
        return income_total(window, org_id, db_name)["summary"]["total_income"]
    if base == "expenses":
        return expense_total(window, org_id, db_name)["summary"]["total_expenses"]
    return profit_summary(window, org_id, db_name)["summary"]["net_profit"]


def _previous_window(start: str, end: str, basis: str) -> tuple[str, str]:
    """The window to compare against: same dates last year, or the equal-length window just before."""
    s = datetime.date.fromisoformat(start)
    e = datetime.date.fromisoformat(end)
    if basis == "year":
        def back_a_year(d: datetime.date) -> datetime.date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29 Feb
                return d.replace(year=d.year - 1, day=28)
        return back_a_year(s).isoformat(), back_a_year(e).isoformat()
    prev_end = s - datetime.timedelta(days=1)
    if s.day == 1 and (e + datetime.timedelta(days=1)).day == 1:
        # Whole months (e.g. a quarter): the same number of calendar months before.
        months = (e.year - s.year) * 12 + e.month - s.month + 1
        idx = s.year * 12 + s.month - 1 - months
        return datetime.date(idx // 12, idx % 12 + 1, 1).isoformat(), prev_end.isoformat()
    length = e - s
    return (prev_end - length).isoformat(), prev_end.isoformat()


def period_growth(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Income, expenses or profit this period against a previous one, and the % change.

    params: base ("income" | "expenses" | "profit"), basis ("year": same dates
    last year | "period": the equal-length window just before), start/end.
    growth_pct is None when the previous value is zero (no meaningful %).
    """
    base = str(params.get("base") or "income")
    basis = "year" if str(params.get("basis") or "year") == "year" else "period"
    start, end = _period(params)
    prev_start, prev_end = _previous_window(start, end, basis)
    current = round(_period_value(base, start, end, org_id, db_name), 2)
    previous = round(_period_value(base, prev_start, prev_end, org_id, db_name), 2)
    return {
        "report": "Period Growth",
        "period": {"start_date": start, "end_date": end},
        "previous_period": {"start_date": prev_start, "end_date": prev_end},
        "summary": {
            "base": base,
            "basis": basis,
            "current": current,
            "previous": previous,
            "change": round(current - previous, 2),
            "growth_pct": round(100.0 * (current - previous) / abs(previous), 2) if previous else None,
        },
    }


def metric_series(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Income, expenses or profit per month or quarter over a window.

    params: base ("income" | "expenses" | "profit"), grain ("month" |
    "quarter"), start/end. Periods with no activity are returned as 0 so every
    organization has the same periods.
    """
    base = str(params.get("base") or "income")
    grain = "quarter" if str(params.get("grain") or "month") == "quarter" else "month"
    start, end = _period(params)

    def per_period(table: str, alias: str, items: str, fk: str, date_col: str) -> Dict[str, float]:
        rows = query(
            f"""
            SELECT DATE_TRUNC(%s, CAST({alias}.{date_col} AS DATE))::date AS period_start,
                   COALESCE(SUM(it.line_amount), 0)                       AS amount
            FROM   {table} {alias}
            JOIN   {items} it     ON it.{fk} = {alias}.id
            JOIN   status_type st ON st.id = {alias}.status_type_id
            WHERE  {alias}.organization_id = %s
              AND  st.value NOT IN ('CANCELLED','VOIDED')
              AND  CAST({alias}.{date_col} AS DATE) BETWEEN %s AND %s
            GROUP  BY 1
            """,
            (grain, org_id, start, end),
            org_id,
            db_name,
        )
        return {str(r.get("period_start"))[:10]: float(r.get("amount") or 0) for r in rows}

    income = per_period("income", "inc", "income_items", "income_id", "invoice_date") if base != "expenses" else {}
    expenses = per_period("expense", "e", "expense_items", "expense_id", "reception_date") if base != "income" else {}

    def label(day: datetime.date) -> str:
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}" if grain == "quarter" else f"{day.year}-{day.month:02d}"

    periods: List[Dict[str, Any]] = []
    day = datetime.date.fromisoformat(start).replace(day=1)
    if grain == "quarter":
        day = day.replace(month=((day.month - 1) // 3) * 3 + 1)
    last = datetime.date.fromisoformat(end)
    step = 3 if grain == "quarter" else 1
    while day <= last:
        key = day.isoformat()
        if base == "income":
            value = income.get(key, 0.0)
        elif base == "expenses":
            value = expenses.get(key, 0.0)
        else:
            value = income.get(key, 0.0) - expenses.get(key, 0.0)
        periods.append({"period": label(day), "value": round(value, 2)})
        month = day.month - 1 + step
        day = day.replace(year=day.year + month // 12, month=month % 12 + 1)
    return {
        "report": "Metric Series",
        "period": {"start_date": start, "end_date": end},
        "summary": {"base": base, "grain": grain, "total": round(sum(p["value"] for p in periods), 2),
                    "period_count": len(periods)},
        "series": periods,
    }


def balance_sheet(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Assets, liabilities and equity as of a date, from posted ledger entries.

    Balances by account type: assets as debit minus credit; liabilities and
    equity as credit minus debit. Revenue and expense accounts are closed into
    equity as accumulated earnings, so assets = liabilities + equity whenever
    the ledger balances (`difference` reports any gap rather than hiding it).
    Replaces the Accutax /report/balance-sheet call for multi-org questions:
    that endpoint times out with several organizations.
    """
    as_of = _as_of(params)
    rows = query(
        """
        SELECT coa.account_type,
               coa.account_sub_type,
               coa.account_name,
               COALESCE(SUM(l.debit_amount - l.credit_amount), 0) AS debit_balance
        FROM   chart_of_accounts coa
        JOIN   journal_entry_lines l ON l.account_id = coa.id
        JOIN   journal_entries j     ON j.id = l.journal_entry_id
        WHERE  coa.organization_id = %s
          AND  j.organization_id  = %s
          AND  j.is_posted
          AND  CAST(j.transaction_date AS DATE) <= %s::DATE
        GROUP  BY coa.account_type, coa.account_sub_type, coa.account_name
        """,
        (org_id, org_id, as_of),
        org_id,
        db_name,
    )
    sections: Dict[str, List[Dict[str, Any]]] = {"Asset": [], "Liability": [], "Equity": []}
    earnings = 0.0
    for r in rows:
        kind = r.get("account_type")
        debit_balance = float(r.get("debit_balance") or 0)
        if kind in ("Revenue", "Expense"):
            earnings -= debit_balance  # revenue credits raise it, expense debits lower it
            continue
        if kind not in sections:
            continue
        balance = debit_balance if kind == "Asset" else -debit_balance
        sections[kind].append({"account": r.get("account_name"), "sub_type": r.get("account_sub_type"),
                               "balance": round(balance, 2)})
    sections["Equity"].append({"account": "Accumulated earnings", "sub_type": "Equity",
                               "balance": round(earnings, 2)})
    totals = {k: round(sum(a["balance"] for a in v), 2) for k, v in sections.items()}
    return {
        "report": "Balance Sheet",
        "as_of": as_of,
        "summary": {
            "total_assets": totals["Asset"],
            "total_liabilities": totals["Liability"],
            "total_equity": totals["Equity"],
            "difference": round(totals["Asset"] - totals["Liability"] - totals["Equity"], 2),
        },
        "assets": sorted(sections["Asset"], key=lambda a: -abs(a["balance"])),
        "liabilities": sorted(sections["Liability"], key=lambda a: -abs(a["balance"])),
        "equity": sorted(sections["Equity"], key=lambda a: -abs(a["balance"])),
    }


def _open_balance(table: str, date_col: str, org_id: int, as_of: str, db_name: str) -> Dict[str, Any]:
    """Outstanding and overdue amounts of open (PENDING / PARTIALLY_PAID) documents.

    Payment state lives in the status; amount_paid is only subtracted for
    partly paid documents' sake. Totals include tax, like rpt_invoice_list.
    """
    alias, items, fk = ("inc", "income_items", "income_id") if table == "income" else ("e", "expense_items", "expense_id")
    rows = query(
        f"""
        SELECT COALESCE(SUM(t.total - t.amount_paid), 0)                           AS outstanding,
               COUNT(*)                                                            AS open_count,
               COALESCE(SUM(t.total - t.amount_paid) FILTER (WHERE t.due_date < %s::DATE), 0) AS overdue_amount,
               COUNT(*) FILTER (WHERE t.due_date < %s::DATE)                       AS overdue_count
        FROM (
            SELECT {alias}.id,
                   CAST({alias}.due_date AS DATE)          AS due_date,
                   COALESCE({alias}.amount_paid, 0)         AS amount_paid,
                   COALESCE(SUM(CAST(it.line_amount AS DECIMAL)
                                + COALESCE(CAST(it.tax_amount AS DECIMAL), 0)), 0) AS total
            FROM   {table} {alias}
            JOIN   {items} it     ON it.{fk} = {alias}.id
            JOIN   status_type st ON st.id = {alias}.status_type_id
            WHERE  {alias}.organization_id = %s
              AND  st.value IN ('PENDING','PARTIALLY_PAID')
              AND  CAST({alias}.{date_col} AS DATE) <= %s::DATE
            GROUP  BY {alias}.id, {alias}.due_date, {alias}.amount_paid
        ) t
        """,
        (as_of, as_of, org_id, as_of),
        org_id,
        db_name,
    )
    row = rows[0] if rows else {}
    return {
        "outstanding": round(float(row.get("outstanding") or 0), 2),
        "open_count": int(row.get("open_count") or 0),
        "overdue_amount": round(float(row.get("overdue_amount") or 0), 2),
        "overdue_count": int(row.get("overdue_count") or 0),
    }


def receivables_outstanding(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """What customers owe as of a date: open invoices, and the overdue part."""
    as_of = _as_of(params)
    return {"report": "Receivables Outstanding", "as_of": as_of,
            "summary": _open_balance("income", "invoice_date", org_id, as_of, db_name)}


def payables_outstanding(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """What the organization owes suppliers as of a date: open bills, and the overdue part."""
    as_of = _as_of(params)
    return {"report": "Payables Outstanding", "as_of": as_of,
            "summary": _open_balance("expense", "reception_date", org_id, as_of, db_name)}


def cash_balance(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Cash and bank position as of a date, from the general ledger.

    The balance is debit minus credit on the organization's cash and bank
    asset accounts (Bank Account, Cash on Hand, Petty Cash...) over posted
    journal entries. The bank_accounts table is not used: it holds rows for
    only a few organizations, while every organization posts to its ledger.

    total_balance is set only when every account uses one currency; mixed
    accounts are reported per currency and never added together.
    """
    as_of = _as_of(params)
    rows = query(
        """
        SELECT coa.account_name,
               COALESCE(coa.currency, '')                                   AS currency,
               COALESCE(SUM(l.debit_amount - l.credit_amount), 0)           AS balance
        FROM   chart_of_accounts coa
        JOIN   journal_entry_lines l ON l.account_id = coa.id
        JOIN   journal_entries j     ON j.id = l.journal_entry_id
        WHERE  coa.organization_id = %s
          AND  j.organization_id  = %s
          AND  coa.account_type = 'Asset'
          AND  (coa.account_name ILIKE '%%cash%%' OR coa.account_name ILIKE '%%bank%%')
          AND  j.is_posted
          AND  CAST(j.transaction_date AS DATE) <= %s::DATE
        GROUP  BY coa.account_name, coa.currency
        ORDER  BY balance DESC
        """,
        (org_id, org_id, as_of),
        org_id,
        db_name,
    )
    accounts = [{"account": r.get("account_name"), "currency": r.get("currency") or "",
                 "balance": round(float(r.get("balance") or 0), 2)} for r in rows]
    by_currency: Dict[str, float] = {}
    for a in accounts:
        key = a["currency"] or "unknown"
        by_currency[key] = round(by_currency.get(key, 0.0) + a["balance"], 2)
    single = len(by_currency) == 1
    return {
        "report": "Cash Balance",
        "as_of": as_of,
        "summary": {
            "account_count": len(accounts),
            "by_currency": by_currency,
            "total_balance": next(iter(by_currency.values())) if single else None,
            "account_currency": (next(iter(by_currency)) if single and next(iter(by_currency)) != "unknown" else None),
        },
        "accounts": accounts,
    }


def _contact_totals(kind: str, params: Dict[str, Any], org_id: int, db_name: str) -> Dict[str, Any]:
    """Totals per vendor (bills) or per customer (invoices), largest first.

    Used by multi-org questions about vendors or customers the organizations
    share: names are matched across orgs, so each org's list must be long,
    not a top 20. The row keys (vendor/customer, total_*) are read by
    orchestrator/multi_org_present.py.
    """
    if kind == "vendor":
        table, alias, items, fk, date_col = "expense", "e", "expense_items", "expense_id", "reception_date"
        name, count, total, label = "vendor", "bill_count", "total_spend", "Vendor Totals"
    else:
        table, alias, items, fk, date_col = "income", "inc", "income_items", "income_id", "invoice_date"
        name, count, total, label = "customer", "invoice_count", "total_sales", "Customer Totals"
    start, end = _period(params)
    limit = _limit(params, default=500, ceiling=500)
    rows = query(
        f"""
        SELECT COALESCE(c.name, c.organization_name, 'Unknown') AS {name},
               COUNT(DISTINCT {alias}.id)                       AS {count},
               COALESCE(SUM(it.line_amount), 0)                 AS {total}
        FROM   {table} {alias}
        JOIN   {items} it     ON it.{fk} = {alias}.id
        JOIN   contacts c     ON c.id = {alias}.contact_id
        JOIN   status_type st ON st.id = {alias}.status_type_id
        WHERE  {alias}.organization_id = %s
          AND  st.value NOT IN ('CANCELLED','VOIDED')
          AND  CAST({alias}.{date_col} AS DATE) BETWEEN %s AND %s
        GROUP  BY c.organization_name, c.name
        ORDER  BY {total} DESC
        LIMIT  %s
        """,
        (org_id, start, end, limit),
        org_id,
        db_name,
    )
    return {
        "report": label,
        "period": {"start_date": start, "end_date": end},
        "summary": {"contact_count": len(rows), "total": total_of(rows, total)},
        f"{name}s": rows,
    }


# ── Filtered document list (multi-org lists) ────────────────────────────────

#: status filter -> SQL condition on status_type.value. Open means payment is due.
_DOCUMENT_STATUS_SQL = {
    "default": "st.value NOT IN ('CANCELLED','VOIDED')",
    "open": "st.value IN ('PENDING','PARTIALLY_PAID')",
    "paid": "st.value = 'PAID'",
    "cancelled": "st.value IN ('CANCELLED','VOIDED')",
    "all": "TRUE",
}
_DOCUMENT_ORDER_SQL = {
    "amount_desc": "amount DESC, doc_date DESC",
    "amount_asc": "amount ASC, doc_date DESC",
    "date_desc": "doc_date DESC, amount DESC",
    "date_asc": "doc_date ASC, amount DESC",
}


def _number_param(params: Dict[str, Any], key: str) -> Any:
    try:
        value = params.get(key)
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_date_sql(column: str) -> str:
    """A date column cast that yields NULL for text that is not a date.

    Seed data holds at least one due_date of "string" (a Swagger placeholder,
    org 24). A plain CAST fails the whole query, so one bad row would make an
    organization "could not be retrieved".
    """
    text = f"CAST({column} AS TEXT)"
    return f"(CASE WHEN {text} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' THEN CAST(LEFT({text}, 10) AS DATE) END)"


def document_list(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    """Invoices or bills of one organization, filtered and sorted in SQL.

    Multi-org list questions ("top 10 largest bills", "unpaid invoices older
    than 180 days", "bills over 50,000") used to fetch the newest 20 rows and
    leave the filtering to a model, which could not find the largest bills
    among the newest, kept cancelled bills, and listed recent paid invoices
    as "unpaid, older than 180 days".

    params: kind ("invoice" | "bill"), status (see _DOCUMENT_STATUS_SQL),
    overdue (due date passed), min_amount / max_amount, older_than_days
    (document date), overdue_days (due date at least that many days ago),
    start_date / end_date (optional; all history without them), order (see
    _DOCUMENT_ORDER_SQL), limit. The amount is the open balance for open
    documents and the total otherwise. The summary counts every match, not
    only the listed rows.
    """
    kind = "bill" if str(params.get("kind") or "") == "bill" else "invoice"
    if kind == "bill":
        table, items, fk, number, date_col, contact = "expense", "expense_items", "expense_id", "receipt_number", "reception_date", "vendor"
    else:
        table, items, fk, number, date_col, contact = "income", "income_items", "income_id", "invoice_number", "invoice_date", "customer"
    doc_date, due_date = _safe_date_sql(f"d.{date_col}"), _safe_date_sql("d.due_date")
    status = str(params.get("status") or "default")
    status_sql = _DOCUMENT_STATUS_SQL.get(status, _DOCUMENT_STATUS_SQL["default"])
    order = str(params.get("order") or "")
    order = order if order in _DOCUMENT_ORDER_SQL else "amount_desc"
    order_by = _DOCUMENT_ORDER_SQL[order]
    limit = _limit(params, default=25, ceiling=200)
    today = datetime.date.today()
    basis = "balance" if status == "open" else "total"

    where = [f"d.organization_id = %s", status_sql]
    args: List[Any] = [org_id]
    if params.get("start_date") or params.get("end_date"):
        start, end = _period(params)
        where.append(f"{doc_date} BETWEEN %s AND %s")
        args += [start, end]
    else:
        # All history means up to today, as in the outstanding reports:
        # documents dated in the future are not counted yet.
        where.append(f"{doc_date} <= %s::DATE")
        args.append(today.isoformat())
    older = _number_param(params, "older_than_days")
    if older is not None:
        where.append(f"{doc_date} <= %s::DATE")
        args.append((today - datetime.timedelta(days=int(older))).isoformat())
    overdue_days = _number_param(params, "overdue_days")
    if params.get("overdue") or overdue_days is not None:
        cutoff = today - datetime.timedelta(days=int(overdue_days or 0))
        where.append(f"{due_date} < %s::DATE")
        args.append(cutoff.isoformat() if overdue_days else today.isoformat())

    amount_where, amount_args = [], []
    low, high = _number_param(params, "min_amount"), _number_param(params, "max_amount")
    if low is not None:
        amount_where.append("amount > %s")
        amount_args.append(low)
    if high is not None:
        amount_where.append("amount < %s")
        amount_args.append(high)
    amount_sql = ("WHERE " + " AND ".join(amount_where)) if amount_where else ""

    per_doc = f"""
        SELECT *, {'total - amount_paid' if basis == 'balance' else 'total'} AS amount
        FROM (
            SELECT d.id,
                   d.{number}                                          AS number,
                   COALESCE(c.name, c.organization_name, 'Unknown')    AS {contact},
                   {doc_date}                                          AS doc_date,
                   {due_date}                                          AS due_date,
                   st.value                                            AS status,
                   COALESCE(SUM(CAST(it.line_amount AS DECIMAL)
                                + COALESCE(CAST(it.tax_amount AS DECIMAL), 0)), 0) AS total,
                   COALESCE(d.amount_paid, 0)                          AS amount_paid
            FROM   {table} d
            JOIN   {items} it     ON it.{fk} = d.id
            JOIN   status_type st ON st.id = d.status_type_id
            LEFT   JOIN contacts c ON c.id = d.contact_id
            WHERE  {' AND '.join(where)}
            GROUP  BY d.id, d.{number}, c.name, c.organization_name, d.{date_col}, d.due_date, st.value, d.amount_paid
        ) docs
    """
    rows = query(
        f"SELECT * FROM ({per_doc}) t {amount_sql} ORDER BY {order_by} LIMIT %s",
        (*args, *amount_args, limit),
        org_id,
        db_name,
    )
    totals = query(
        f"SELECT COUNT(*) AS match_count, COALESCE(SUM(amount), 0) AS matched_amount FROM ({per_doc}) t {amount_sql}",
        (*args, *amount_args),
        org_id,
        db_name,
    )
    agg = totals[0] if totals else {}

    def age(value: Any) -> Any:
        # Rows come back serialized: dates are ISO strings.
        try:
            return (today - datetime.date.fromisoformat(str(value)[:10])).days
        except ValueError:
            return None

    listed = [{
        f"{kind}_number": r.get("number"),
        contact: r.get(contact),
        basis: round(float(r.get("amount") or 0), 2),
        f"{kind}_date": str(r.get("doc_date") or "")[:10],
        "due_date": str(r.get("due_date") or "")[:10],
        "status": r.get("status"),
        "age_days": age(r.get("doc_date")),
    } for r in rows]
    return {
        "report": "Invoice List" if kind == "invoice" else "Bill List",
        "summary": {
            "match_count": int(agg.get("match_count") or 0),
            "matched_amount": round(float(agg.get("matched_amount") or 0), 2),
            "listed": len(listed),
            "amount_key": basis,
            "name_key": contact,
            "date_key": f"{kind}_date",
            "order": order,
            "sorted": True,
        },
        f"{kind}s": listed,
    }


def vendor_totals(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    return _contact_totals("vendor", params, org_id, db_name)


def customer_totals(params: Dict[str, Any], org_id: int, db_name: str = "") -> Dict[str, Any]:
    return _contact_totals("customer", params, org_id, db_name)


#: endpoint -> report function. The `rpt_` prefix is what the orchestrator's
#: _retrieve() dispatches on, mirroring the existing `fn_` convention.
REPORTS: Dict[str, Callable[[Dict[str, Any], int, str], Dict[str, Any]]] = {
    "rpt_aged_receivables_detail": aged_receivables_detail,
    "rpt_aged_payables_detail": aged_payables_detail,
    "rpt_bills_by_contact": bills_by_contact,
    "rpt_expenses_by_contact": expenses_by_contact,
    "rpt_supplier_statement": supplier_statement,
    "rpt_profit_loss_by_project": profit_loss_by_project,
    "rpt_profit_loss_by_cost_center": profit_loss_by_cost_center,
    "rpt_sales_by_project": sales_by_project,
    "rpt_estimate_conversion": estimate_conversion,
    "rpt_vat_input_output": vat_input_output,
    "rpt_vat_export_return": vat_export_return,
    "rpt_income_total": income_total,
    "rpt_invoice_list": invoice_list,
    "rpt_document_list": document_list,
    "rpt_expense_total": expense_total,
    "rpt_profit_summary": profit_summary,
    "rpt_receivables_outstanding": receivables_outstanding,
    "rpt_payables_outstanding": payables_outstanding,
    "rpt_cash_balance": cash_balance,
    "rpt_period_growth": period_growth,
    "rpt_metric_series": metric_series,
    "rpt_balance_sheet": balance_sheet,
    "rpt_vendor_totals": vendor_totals,
    "rpt_customer_totals": customer_totals,
}

#: REST endpoints that ignore organization_id -> org-filtered SQL report with
#: the same meaning. Used only when a multi-org run skips the REST call
#: (reason "not_org_scoped"); single-org queries never reach it.
ORG_SCOPED_SUBSTITUTES: Dict[str, str] = {
    "/income/list": "rpt_invoice_list",
    "/income/total": "rpt_income_total",
}

# Nest /report/* path -> SQL rpt_ used only when the REST call is unavailable.
REST_TO_SQL_REPORT: Dict[str, str] = {
    "/report/invoice-details": "rpt_aged_receivables_detail",
    "/report/aged-payables-detail": "rpt_aged_payables_detail",
    "/report/bills-by-contact": "rpt_bills_by_contact",
    "/report/expenses-by-contact": "rpt_expenses_by_contact",
    "/report/supplier-statement-of-account": "rpt_supplier_statement",
    "/report/profit-loss-by-project": "rpt_profit_loss_by_project",
    "/report/profit-loss-by-cost-center": "rpt_profit_loss_by_cost_center",
    "/report/sales-by-project": "rpt_sales_by_project",
    "/report/estimate-conversion-rate": "rpt_estimate_conversion",
    "/report/vat-input-output": "rpt_vat_input_output",
    "/report/export-return": "rpt_vat_export_return",
}
