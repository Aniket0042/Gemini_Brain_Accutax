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
}
