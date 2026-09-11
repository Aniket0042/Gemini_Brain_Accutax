"""
formatters.py — Deterministic markdown table/card renderers for Gemini Brain tools.

Formats numbers as 'AED 1,234,567.00'.
Every tool result is narrated by an LLM before reaching the user (see
orchestrator/gemini_brain_runner.py); this module's table is exposed alongside
the narration as `table_markdown`, and is also the deterministic fallback used
if narration itself fails.
"""
from __future__ import annotations

import datetime as dt
import numbers
import re
from typing import Any, Dict, List, Optional


def format_aed(val: Any) -> str:
    """Format numeric values as AED currency string."""
    if val is None or val == "":
        return "AED 0.00"
    try:
        num = float(val)
        return f"AED {num:,.2f}"
    except (ValueError, TypeError):
        return str(val)


#: Envelope/status keys that show up when an upstream `{"code":.., "message":..,
#: "details":..}`-shaped response leaks into a formatter unwrapped. extract_data_safe
#: strips these under normal operation; this is defense in depth, not the primary fix.
_ENVELOPE_KEYS = frozenset({"code", "message", "success", "status"})

#: Nested list the API wraps around the actual rows. `report` is the
#: aged-payables-detail envelope; without unwrapping it the UI dumps the
#: whole array into one "Report" KPI cell.
_LIST_UNWRAP_KEYS = (
    "items", "results", "invoices", "bills", "transactions", "contacts",
    "data", "report", "entries", "records", "rows",
)
_ID_KEYS = frozenset({
    "id", "contact", "contact_id", "transaction_id", "transactionid",
    "vendor_id", "customer_id", "bill_id", "invoice_id",
})
_MONEY_HINTS = (
    "amount", "balance", "price", "debit", "credit", "revenue", "expense",
    "income", "profit", "paid", "outstanding", "spend", "cost", "tax", "vat",
    "fee", "payment", "receipt", "sales", "cogs", "gross", "net", "liability",
    "cash",
)


def _norm_key(key: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key or "")
    return s.lower().replace(" ", "_")


def _column_label(key: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key or "")
    return s.replace("_", " ").title()


def _is_id_key(key: str) -> bool:
    n = _norm_key(key)
    return n in _ID_KEYS or n.endswith("_id")


def _is_days_key(key: str) -> bool:
    return "days" in _norm_key(key)


def _is_money_key(key: str) -> bool:
    n = _norm_key(key)
    if _is_id_key(key) or _is_days_key(key):
        return False
    if n == "count" or n.endswith("_count") or "quantity" in n or n.endswith("_qty"):
        return False
    if any(h in n for h in _MONEY_HINTS):
        return True
    return n == "total" or n.endswith("_total") or n.startswith("total_")


def _is_date_key(key: str) -> bool:
    n = _norm_key(key)
    if n in ("date", "due_date", "transaction_date", "as_of_date", "posted_date"):
        return True
    return n.endswith("_date") and "update" not in n


def _format_display_date(val: Any) -> str:
    """ISO dates become '11 Sep 2026'; already-human strings are left as-is."""
    if val is None or val == "" or val == "-":
        return "-"
    if isinstance(val, dt.datetime):
        val = val.date()
    if isinstance(val, dt.date):
        return f"{val.day} {val.strftime('%b')} {val.year}"
    s = str(val).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return s or "-"
    try:
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return s
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _int_display(val: Any) -> str:
    if val is None or val == "" or val == "-":
        return "-"
    try:
        return f"{int(float(val)):,}"
    except (TypeError, ValueError):
        return str(val).strip() or "-"


def _format_cell_value(key: str, val: Any) -> str:
    if isinstance(val, (dict, list)):
        return _clean_str(val)
    if _is_date_key(key):
        return _format_display_date(val)
    if _is_days_key(key) or _is_id_key(key):
        return _int_display(val)
    if _is_money_key(key):
        return format_aed(val)
    return _clean_str(val)


def _unwrap_row_list(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    for k in _LIST_UNWRAP_KEYS:
        v = data.get(k)
        if isinstance(v, list):
            return v
    return data


def _is_total_row(row: Dict[str, Any]) -> bool:
    return str(row.get("row_type") or row.get("rowType") or "").lower() == "total"


def _is_aging_matrix_rows(rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return False
    return any(str(k).startswith("bin_") for k in rows[0].keys())


def _row_get(row: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return default


def _format_period(v: Any) -> Optional[str]:
    """Render a {'start_date': ..., 'end_date': ...}-shaped dict as a date range.

    Returns None if `v` isn't a period-shaped dict, so callers can fall through
    to normal handling instead of leaking the dict's raw repr into a table cell.
    """
    if isinstance(v, dict) and ("start_date" in v or "end_date" in v):
        start = v.get("start_date") or "?"
        end = v.get("end_date") or "?"
        return f"{start} to {end}"
    return None


def _clean_str(val: Any, _depth: int = 0) -> str:
    """Render any scalar/dict/list value as readable text — never a raw Python repr.

    This is the shared fallback every formatter reaches for a value it doesn't
    have a dedicated renderer for. Without it, any nested dict (a "period"
    sub-object, or any other upstream API detail we haven't special-cased)
    leaks straight into a table cell as Python's `str(dict)` — single-quoted,
    not valid JSON, and visibly a debugging artifact to an end user.
    """
    if val is None:
        return "-"
    period = _format_period(val)
    if period is not None:
        return period
    if isinstance(val, dict):
        if not val:
            return "-"
        if _depth >= 2:
            return f"({len(val)} fields)"
        parts = []
        for k, v in val.items():
            display = _format_cell_value(k, v) if not isinstance(v, (dict, list)) else _clean_str(v, _depth + 1)
            parts.append(f"{_column_label(k)}: {display}")
        return ", ".join(parts)
    if isinstance(val, list):
        if not val:
            return "-"
        if _depth >= 2:
            return f"({len(val)} items)"
        shown = ", ".join(_clean_str(v, _depth + 1) for v in val[:10])
        return shown + (f" (+{len(val) - 10} more)" if len(val) > 10 else "")
    s = str(val).strip()
    return s if s else "-"


#: Columns that are never useful in a chat table (nested graphs, tenant ids).
_TABLE_SKIP_KEYS = frozenset({
    "lines", "user", "organization", "user_id", "organization_id", "updated_at",
    "posted_by", "source_id", "reversed_entry_id", "is_reversal", "reversal_reason",
    "transaction_id", "transactionid", "transaction_type", "transactiontype",
    "currency", "currency_code", "row_type", "rowtype",
})
_TABLE_PRIORITY_KEYS = [
    "journal_number", "reference_number", "transaction_date", "description",
    "source_type", "total_debit", "total_credit",
    "invoice_number", "bill_number", "name", "contact_name", "contactName",
    "reference", "category",
    "date", "due_date", "dueDate", "days_overdue", "daysOverdue", "bucket",
    "amount", "total", "balance", "status", "created_at",
]


def _row_column_keys(first: Dict[str, Any], cap: int = 8) -> List[str]:
    """Pick a readable subset of columns from the first row of a list payload."""
    usable = []
    for k, v in first.items():
        nk = _norm_key(k)
        if k in _TABLE_SKIP_KEYS or nk in _TABLE_SKIP_KEYS or _is_display_noise_key(k):
            continue
        if isinstance(v, (dict, list)):
            continue
        if _is_id_key(k) and _looks_numeric(v):
            continue
        usable.append(k)
    if not usable:
        usable = [k for k, v in first.items() if not isinstance(v, (dict, list))]
    ordered = [k for k in _TABLE_PRIORITY_KEYS if k in usable]
    ordered += [k for k in usable if k not in _TABLE_PRIORITY_KEYS]
    return ordered[:cap]


def render_kv_summary(data: Any) -> str:
    """Render key-value dictionary as a markdown summary card/table."""
    if not isinstance(data, dict):
        return render_row_table(data)

    items = _kpi_items_from_dict(data)
    if not items:
        return "_No records found._"

    lines = ["| Metric | Value |", "|---|---|"]
    for item in items:
        prefix = f"{item['section']} · " if item.get("section") else ""
        lines.append(f"| **{prefix}{item['label']}** | {item['value']} |")
    return "\n".join(lines)


def _get_any(d: Dict[str, Any], *candidates: str, default: Any = None) -> Any:
    """Return the first key present in `d` from `candidates`, else `default`."""
    for c in candidates:
        if c in d:
            return d[c]
    return default


def render_dashboard_overview(data: Any) -> str:
    """Render /dashboard/web's overview payload.

    Shape: {"year", "dateRange"/"date_range": {"start","end"}, "graphData"/
    "graph_data": {"labels": [...], "incomeValues"/"income_values": [...],
    "expenseValues": [...], "cashflowValues": [...]}, "totals": {"income",
    "expense", "cashflow", "tax", "estimation"}}.

    The real API's exact key casing isn't confirmed (couldn't reach it live —
    known token-expiry issue, unrelated to this fix), but title-casing "Income
    Values" would show a space if the source key were snake_case; the observed
    "Incomevalues" (no space) proves it isn't. So this tries both camelCase and
    plain-lowercase spellings for every ambiguous key rather than guess wrong,
    same way it would look with an underscore if that turns out right too.
    """
    if not isinstance(data, dict):
        return render_row_table(data)

    sections: List[str] = []

    date_range = _get_any(data, "dateRange", "date_range", "daterange")
    period = _format_period(date_range) if isinstance(date_range, dict) else None
    if period is None and isinstance(date_range, dict):
        start = _get_any(date_range, "start", "start_date", default="?")
        end = _get_any(date_range, "end", "end_date", default="?")
        period = f"{start} to {end}"
    if period:
        sections.append(f"_Period: {period}_")

    graph = _get_any(data, "graphData", "graph_data", "graphdata")
    if isinstance(graph, dict):
        labels = graph.get("labels") or []
        income = _get_any(graph, "incomeValues", "income_values", "incomevalues", default=[]) or []
        expense = _get_any(graph, "expenseValues", "expense_values", "expensevalues", default=[]) or []
        cashflow = _get_any(graph, "cashflowValues", "cashflow_values", "cashflowvalues", default=[]) or []
        if labels:
            lines = ["| Month | Income | Expense | Cash Flow |", "|---|---|---|---|"]
            for i, label in enumerate(labels):
                inc = income[i] if i < len(income) else 0
                exp = expense[i] if i < len(expense) else 0
                cf = cashflow[i] if i < len(cashflow) else 0
                lines.append(f"| **{label}** | {format_aed(inc)} | {format_aed(exp)} | {format_aed(cf)} |")
            sections.append("\n".join(lines))

    totals = data.get("totals")
    if isinstance(totals, dict) and totals:
        lines = ["| Metric | Value |", "|---|---|"]
        for k, v in totals.items():
            lines.append(f"| **{k.replace('_', ' ').title()}** | {format_aed(v)} |")
        sections.append("\n".join(lines))

    if not sections:
        return render_kv_summary(data)
    return "\n\n".join(sections)


def render_row_table(data: Any, max_rows: int = 50) -> str:
    """Render a list of dictionaries as a clean markdown table."""
    if isinstance(data, dict) and isinstance(data.get("report"), list):
        return render_aging_buckets(data)

    if isinstance(data, dict):
        data = _unwrap_row_list(data)

    if not isinstance(data, list) or not data:
        if isinstance(data, dict):
            return render_kv_summary(data)
        return "_No records found._"

    first = data[0]
    if not isinstance(first, dict):
        lines = ["| Value |", "|---|"]
        for item in data[:max_rows]:
            lines.append(f"| {_clean_str(item)} |")
        return "\n".join(lines)

    selected_keys = _row_column_keys(first)

    header_labels = [_column_label(k) for k in selected_keys]
    lines = [
        "| " + " | ".join(header_labels) + " |",
        "| " + " | ".join(["---"] * len(selected_keys)) + " |",
    ]

    for row in data[:max_rows]:
        row_vals = [_format_cell_value(k, row.get(k)) for k in selected_keys]
        lines.append("| " + " | ".join(row_vals) + " |")

    if len(data) > max_rows:
        lines.append(f"\n_Showing {max_rows} of {len(data)} total records._")

    return "\n".join(lines)


def _bin_label_map(bins: List[Dict[str, Any]]) -> Dict[str, str]:
    return {str(b["id"]): str(b.get("label") or b["id"]) for b in bins if b.get("id")}


def _aging_detail_table(data_rows: List[Dict[str, Any]], bins: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bill-level aged-payables rows (contactName, reference, amount, bucket)."""
    labels = _bin_label_map(bins)
    columns = [
        {"key": "vendor", "label": "Vendor", "align": "left"},
        {"key": "reference", "label": "Reference", "align": "left"},
        {"key": "date", "label": "Date", "align": "left"},
        {"key": "due_date", "label": "Due Date", "align": "left"},
        {"key": "days_overdue", "label": "Days Overdue", "align": "right"},
        {"key": "bucket", "label": "Bucket", "align": "left"},
        {"key": "amount", "label": "Amount", "align": "right"},
    ]
    rows = []
    total = 0.0
    for r in data_rows:
        amt = _row_get(r, "amount", default=0) or 0
        try:
            total += float(amt)
        except (TypeError, ValueError):
            pass
        bucket_id = _row_get(r, "bucket", "aging_bucket", "bin")
        rows.append({
            "vendor": _row_get(r, "contact_name", "contactName", "name", default="-"),
            "reference": _row_get(r, "reference", "reference_number", default="-"),
            "date": _format_display_date(_row_get(r, "date", "transaction_date")),
            "due_date": _format_display_date(_row_get(r, "due_date", "dueDate")),
            "days_overdue": _int_display(_row_get(r, "days_overdue", "daysOverdue")),
            "bucket": labels.get(str(bucket_id), bucket_id) if bucket_id else "-",
            "amount": format_aed(amt),
        })
    return {"columns": columns, "rows": rows, "total": total}


def _render_aging_detail_markdown(data_rows: List[Dict[str, Any]], bins: List[Dict[str, Any]]) -> str:
    table = _aging_detail_table(data_rows, bins)
    headers = [c["label"] for c in table["columns"]]
    keys = [c["key"] for c in table["columns"]]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in table["rows"]:
        lines.append("| " + " | ".join(str(row[k]) for k in keys) + " |")
    body = "\n".join(lines)
    if table["total"]:
        return f"{body}\n\n**Total Outstanding:** {format_aed(table['total'])}"
    return body


def render_aging_buckets(data: Any) -> str:
    """Render an AR/AP aging report.

    Handles two real Accutax response shapes seen in production:
    1. {"vendors"|"customers": [...], "totals": {"total_outstanding": ...,
       "aging_buckets": {...}}, "period": {...}} — the ar/ap-aging-summary shape.
    2. {"report": [{contact_name, bin_current, bin_1_29, ..., contact_total,
       row_type}, ...], "bins": [{"id", "label"}, ...]} — the aged-payables
       shape, a per-vendor row with one column per aging bucket plus a
       synthetic "total" row. Without this, the generic row/kv formatters
       either can't unwrap "report" at all or flatten every row into one
       unreadable comma-joined cell.
    Falls back to a flat {bucket_name: amount} table for any other
    aging-bucket-shaped payload this formatter might receive.
    """
    if not isinstance(data, dict):
        return render_row_table(data)

    if isinstance(data.get("report"), list):
        report_rows = [r for r in data["report"] if isinstance(r, dict)]
        bins = [b for b in (data.get("bins") or []) if isinstance(b, dict) and b.get("id")]

        data_rows = [r for r in report_rows if not _is_total_row(r)]
        total_row = next((r for r in report_rows if _is_total_row(r)), None)

        if not data_rows:
            return "_No vendors currently have outstanding aged bills._"

        if not _is_aging_matrix_rows(data_rows):
            return _render_aging_detail_markdown(data_rows, bins)

        # Drop bucket columns that are zero for every vendor — keeps the table
        # to the buckets that actually matter instead of 8 mostly-empty columns.
        bin_cols = [
            (b["id"], b.get("label") or b["id"])
            for b in bins
            if any(float(r.get(b["id"]) or 0) != 0 for r in data_rows)
        ]

        headers = ["Vendor"] + [label for _, label in bin_cols] + ["Total"]
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        for row in data_rows:
            name = row.get("contact_name") or row.get("name") or "Unknown"
            vals = [format_aed(row.get(bin_id, 0)) for bin_id, _ in bin_cols]
            total = format_aed(row.get("contact_total", row.get("balance_to_bcy", 0)))
            lines.append("| " + " | ".join([f"**{name}**"] + vals + [total]) + " |")
        table = "\n".join(lines)

        if total_row is not None:
            grand_total = format_aed(total_row.get("contact_total", total_row.get("balance_to_bcy", 0)))
            return f"{table}\n\n**Total Outstanding:** {grand_total}"
        return table

    contact_key = next((k for k in ("vendors", "customers", "contacts") if k in data), None)
    if contact_key is not None:
        sections: List[str] = []
        contacts = data.get(contact_key) or []
        if contacts:
            sections.append(render_row_table(contacts))
        else:
            sections.append(f"_No {contact_key} currently have outstanding aged bills._")

        totals = data.get("totals")
        if isinstance(totals, dict):
            buckets = totals.get("aging_buckets")
            if isinstance(buckets, dict) and buckets:
                lines = ["| Aging Bucket | Outstanding Amount |", "|---|---|"]
                for k, v in buckets.items():
                    lines.append(f"| **{k}** | {format_aed(v)} |")
                sections.append("\n".join(lines))
            total_outstanding = totals.get("total_outstanding")
            if total_outstanding is not None:
                sections.append(f"**Total Outstanding:** {format_aed(total_outstanding)}")

        return "\n\n".join(sections)

    # Fallback: flat {bucket_name: amount} shape, ignoring any leaked envelope keys.
    keys = [k for k in data.keys() if k not in _ENVELOPE_KEYS]
    if not keys:
        return "_No records found._"
    lines = ["| Aging Bucket | Outstanding Amount |", "|---|---|"]
    for k in keys:
        v = data[k]
        period = _format_period(v)
        if period is not None:
            continue
        k_label = k.replace("_", " ").title()
        lines.append(f"| **{k_label}** | {format_aed(v)} |")
    return "\n".join(lines)


def render_account_tree(data: Any) -> str:
    """Render chart of accounts or hierarchy."""
    return render_row_table(data)


def render_financial_statement(data: Any) -> str:
    """Render P&L or Balance Sheet statement."""
    if not isinstance(data, dict):
        return render_row_table(data)

    lines = ["| Line Item | Amount |", "|---|---|"]
    period_caption: Optional[str] = None
    for k, v in data.items():
        if k in _ENVELOPE_KEYS:
            continue
        period = _format_period(v)
        if period is not None:
            period_caption = period
            continue
        if isinstance(v, dict):
            lines.append(f"| **{k.replace('_', ' ').title()}** | |")
            for sub_k, sub_v in v.items():
                sub_period = _format_period(sub_v)
                if sub_period is not None:
                    lines.append(f"| &nbsp;&nbsp;• {sub_k.replace('_', ' ').title()} | {sub_period} |")
                elif isinstance(sub_v, (dict, list)):
                    continue  # avoid leaking an unrecognised nested structure's repr
                else:
                    lines.append(f"| &nbsp;&nbsp;• {sub_k.replace('_', ' ').title()} | {format_aed(sub_v)} |")
        elif isinstance(v, list):
            continue
        else:
            lines.append(f"| **{k.replace('_', ' ').title()}** | {format_aed(v)} |")

    table = "\n".join(lines)
    return f"_Period: {period_caption}_\n\n{table}" if period_caption else table


def render_project_expense_rollup(data: Any) -> str:
    """Render project expense rollup grouped by project and vendor."""
    if isinstance(data, list) and data:
        lines = [
            "| Project Name | Vendor Contact | Bank Account | Transactions | Total Spend |",
            "|---|---|---|---|---|",
        ]
        for row in data:
            if isinstance(row, dict):
                p_name = row.get("project_name") or "Unassigned"
                v_name = row.get("vendor_contact_name") or "Unknown"
                b_name = row.get("bank_account_name") or "N/A"
                cnt = row.get("transaction_count", 0)
                amt = format_aed(row.get("total_spend", 0))
                lines.append(f"| **{p_name}** | {v_name} | {b_name} | {cnt} | {amt} |")
        return "\n".join(lines)
    return render_row_table(data)


def render_inventory_movement(data: Any) -> str:
    """Render inventory movement across warehouse locations, sales, and delivery notes."""
    if isinstance(data, list) and data:
        lines = [
            "| Item Name | SKU | Warehouse | Units Sold (Invoices) | Units Dispatched (Delivery) |",
            "|---|---|---|---|---|",
        ]
        for row in data:
            if isinstance(row, dict):
                item = row.get("item_name") or "Unnamed"
                sku = row.get("sku") or "N/A"
                wh = row.get("warehouse_name") or "Default"
                sold = row.get("units_sold_invoices", 0)
                disp = row.get("units_dispatched_delivery_notes", 0)
                lines.append(f"| **{item}** | {sku} | {wh} | {sold} | {disp} |")
        return "\n".join(lines)
    return render_row_table(data)


def render_gl_profitability(data: Any) -> str:
    """Render GL Account type profitability analysis."""
    if isinstance(data, list) and data:
        lines = [
            "| Account Type | Accounts | Total Income | Total Expense | Net Margin |",
            "|---|---|---|---|---|",
        ]
        for row in data:
            if isinstance(row, dict):
                acct = row.get("account_type") or "General"
                cnt = row.get("account_count", 0)
                inc = format_aed(row.get("total_income", 0))
                exp = format_aed(row.get("total_expense", 0))
                margin = format_aed(row.get("net_margin", 0))
                lines.append(f"| **{acct}** | {cnt} | {inc} | {exp} | **{margin}** |")
        return "\n".join(lines)
    return render_row_table(data)


FORMATTERS = {
    "kv_summary": render_kv_summary,
    "row_table": render_row_table,
    "aging_buckets": render_aging_buckets,
    "account_tree": render_account_tree,
    "financial_statement": render_financial_statement,
    "project_expense_rollup": render_project_expense_rollup,
    "inventory_movement": render_inventory_movement,
    "gl_profitability": render_gl_profitability,
    "dashboard_overview": render_dashboard_overview,
}


def render(formatter_name: str, data: Any, query: Optional[str] = None) -> str:
    """Render data into markdown using the specified formatter.

    `query` is the user's own question text, if available. For the generic
    "row_table" formatter it's used to cap the table to what was actually
    asked for (see utils.ranking.extract_requested_count) — without it, every
    table defaulted to a flat 50 rows regardless of whether the user asked for
    "top 5" or "the largest debtor".
    """
    if data is None or data == [] or data == {}:
        return "_No records found._"
    fn = FORMATTERS.get(formatter_name, render_row_table)
    try:
        if fn is render_row_table and query:
            from gemini_brain.utils.ranking import extract_requested_count
            res = render_row_table(data, max_rows=extract_requested_count(query))
        else:
            res = fn(data)
        try:
            from gemini_brain.formatting.markdown import normalize_markdown
            return normalize_markdown(res)
        except Exception:
            return res
    except Exception as e:
        return f"_Failed to format data: {e}_"


# ══════════════════════════════════════════════════════════════════════════
# Structured blocks — Phase 1 of the UI rebuild (see docs/UI rebuild plan).
#
# `render()` above returns one markdown string; a client can't build a stat
# tile or a sortable table out of that without re-parsing it. `render_blocks`
# returns a JSON-safe list of typed dicts instead — the same underlying data,
# structured enough for the frontend to render each block with real chrome.
#
# This is additive: `render()` keeps working unchanged, existing responses
# are untouched, and only "kv_summary" and "row_table" — the two highest-
# volume formatters — are ported in this phase. Every other formatter name
# falls through to a single markdown block wrapping its existing string
# output, so `blocks` is always well-formed even for a formatter that hasn't
# been ported yet.
#
# `render_kv_summary`/`render_row_table` above are NOT refactored to share
# code with these — they're well-tested markdown producers and this phase
# deliberately doesn't touch them. The two builders below mirror the same
# key-selection logic on purpose; if that logic changes, update both.
# ══════════════════════════════════════════════════════════════════════════

def _looks_numeric(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        return v.replace(".", "", 1).replace("-", "", 1).isdigit()
    return False


#: Nested objects that are a bag of metrics, not a labelled statement section.
_FLAT_METRIC_KEYS = frozenset({"summary", "totals", "metrics", "result"})
#: Envelope field naming the report — not a metric the user needs as a row.
_REPORT_TITLE_KEYS = frozenset({"report"})


def _is_display_noise_key(key: str) -> bool:
    """True for duplicate display-only fields the API sometimes ships alongside the number."""
    k = (key or "").lower().replace(" ", "_")
    return k.startswith("formatted_") or k.endswith("_formatted")


def _is_count_key(key: str) -> bool:
    k = (key or "").lower().replace(" ", "_")
    return k == "count" or k.endswith("_count") or "quantity" in k or k.endswith("_qty") or k.startswith("qty_")


def _format_metric_display(key: str, v: Any) -> tuple[str, Any, bool]:
    """Return (display, raw_value, numeric) for a KPI cell."""
    if _looks_numeric(v):
        if _is_id_key(key) or _is_days_key(key) or _is_count_key(key):
            try:
                n = int(float(v))
                return f"{n:,}", n, True
            except (TypeError, ValueError):
                return _clean_str(v), None, False
        if _is_date_key(key):
            return _format_display_date(v), v, False
        if _is_money_key(key):
            return format_aed(v), v, True
        return _clean_str(v), v, True
    if _is_date_key(key):
        return _format_display_date(v), None, False
    return _clean_str(v), None, False


def _kpi_items_from_dict(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten a report envelope into one KPI row per metric.

    `{report, period, summary: {total_income, invoice_count}}` used to render as
    three rows, with the figures jammed into one unformatted Summary string.
    Period stays a labelled row; nested metrics become their own rows.
    """
    items: List[Dict[str, Any]] = []
    period_caption: Optional[str] = None

    def add_item(key: str, v: Any, section: Optional[str] = None) -> None:
        if _is_display_noise_key(key):
            return
        display, raw, numeric = _format_metric_display(key, v)
        items.append({
            "label": key.replace("_", " ").title(),
            "value": display,
            "raw_value": raw,
            "numeric": numeric,
            "section": section,
        })

    for k, v in data.items():
        period = _format_period(v)
        if period is not None:
            period_caption = period
            continue
        if k.lower() in _REPORT_TITLE_KEYS and isinstance(v, str):
            continue
        if isinstance(v, list):
            continue
        if isinstance(v, dict):
            section = None if k.lower() in _FLAT_METRIC_KEYS else k.replace("_", " ").title()
            for sub_k, sub_v in v.items():
                sub_period = _format_period(sub_v)
                if sub_period is not None:
                    if period_caption is None:
                        period_caption = sub_period
                    continue
                if isinstance(sub_v, (dict, list)):
                    continue
                add_item(sub_k, sub_v, section)
            continue
        add_item(k, v)

    if period_caption:
        items.insert(0, {
            "label": "Period",
            "value": period_caption,
            "raw_value": None,
            "numeric": False,
            "section": None,
        })
    return items


def render_kpi_grid_block(data: Any) -> Dict[str, Any]:
    """Structured counterpart to `render_kv_summary` — one tile per key."""
    if not isinstance(data, dict):
        return render_table_block(data)
    if isinstance(data.get("report"), list):
        blocks = render_aging_buckets_blocks(data)
        return blocks[0] if blocks else {"type": "kpi_grid", "items": []}
    unwrapped = _unwrap_row_list(data)
    if isinstance(unwrapped, list):
        return render_table_block(unwrapped)
    return {"type": "kpi_grid", "items": _kpi_items_from_dict(data)}


def render_chart_block(
    categories: List[str],
    series: List[Dict[str, Any]],
    *,
    chart_type: str = "line",
    x_label: Optional[str] = None,
) -> Dict[str, Any]:
    """A `chart` block: one x-axis of `categories`, one or more named series.

    `series` is `[{"name": "Income", "data": [1000, 2000, ...]}, ...]` — plain
    numbers, not pre-formatted strings, since the frontend needs to scale an
    axis from them, not just print them. Deliberately provider-agnostic: no
    assumption about which charting library (or hand-rolled SVG) renders it.
    """
    return {
        "type": "chart",
        "chart_type": chart_type,
        "x_label": x_label,
        "categories": categories,
        "series": series,
    }


def render_table_block(data: Any, max_rows: int = 50) -> Dict[str, Any]:
    """Structured counterpart to `render_row_table` — real columns and rows,
    not a markdown string, so a client can sort/scroll/export it."""
    if isinstance(data, dict) and isinstance(data.get("report"), list):
        blocks = render_aging_buckets_blocks(data)
        return blocks[0] if blocks else {
            "type": "table", "columns": [], "rows": [], "total_rows": 0, "truncated": False,
        }

    if isinstance(data, dict):
        data = _unwrap_row_list(data)

    if not isinstance(data, list) or not data:
        if isinstance(data, dict):
            return render_kpi_grid_block(data)
        return {"type": "table", "columns": [], "rows": [], "total_rows": 0, "truncated": False}

    first = data[0]
    if not isinstance(first, dict):
        rows = [{"value": _clean_str(item)} for item in data[:max_rows]]
        return {
            "type": "table",
            "columns": [{"key": "value", "label": "Value", "align": "left"}],
            "rows": rows,
            "total_rows": len(data),
            "truncated": len(data) > max_rows,
        }

    selected_keys = _row_column_keys(first)

    columns = []
    for k in selected_keys:
        columns.append({
            "key": k,
            "label": _column_label(k),
            "align": "right" if _is_money_key(k) or _is_days_key(k) else "left",
        })

    rows = []
    for row in data[:max_rows]:
        rows.append({k: _format_cell_value(k, row.get(k)) for k in selected_keys})

    return {
        "type": "table",
        "columns": columns,
        "rows": rows,
        "total_rows": len(data),
        "truncated": len(data) > max_rows,
    }


def render_account_tree_block(data: Any) -> Dict[str, Any]:
    """Structured counterpart to `render_account_tree` — it's a plain table too."""
    return render_table_block(data)


def render_financial_statement_block(data: Any) -> Dict[str, Any]:
    """Structured counterpart to `render_financial_statement` (P&L / balance sheet).

    Mirrors the markdown version's shape-handling: top-level scalars become
    ungrouped tiles, a nested dict becomes a `section`-tagged group of tiles
    (so the frontend can render "Revenue" as a subheading over its own line
    items instead of one flat list), and any period found is exposed as
    `period` alongside the items rather than folded into a synthetic caption.
    """
    if not isinstance(data, dict):
        return render_table_block(data)

    items: List[Dict[str, Any]] = []
    period_caption: Optional[str] = None
    for k, v in data.items():
        if k in _ENVELOPE_KEYS:
            continue
        period = _format_period(v)
        if period is not None:
            period_caption = period
            continue
        label = k.replace("_", " ").title()
        if _is_display_noise_key(k):
            continue
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if _is_display_noise_key(sub_k):
                    continue
                sub_period = _format_period(sub_v)
                if sub_period is not None:
                    items.append({"label": sub_k.replace("_", " ").title(), "value": sub_period,
                                  "raw_value": None, "numeric": False, "section": label})
                elif isinstance(sub_v, (dict, list)):
                    continue
                else:
                    numeric = _looks_numeric(sub_v)
                    items.append({
                        "label": sub_k.replace("_", " ").title(),
                        "value": format_aed(sub_v) if numeric else _clean_str(sub_v),
                        "raw_value": sub_v if numeric else None,
                        "numeric": numeric,
                        "section": label,
                    })
        elif isinstance(v, list):
            continue
        else:
            numeric = _looks_numeric(v)
            items.append({
                "label": label,
                "value": format_aed(v) if numeric else _clean_str(v),
                "raw_value": v if numeric else None,
                "numeric": numeric,
                "section": None,
            })

    block: Dict[str, Any] = {"type": "kpi_grid", "items": items}
    if period_caption:
        block["period"] = period_caption
    return block


def render_aging_buckets_blocks(data: Any) -> List[Dict[str, Any]]:
    """Structured counterpart to `render_aging_buckets`.

    Same three shapes as the markdown version, each mapped to the block type
    that actually fits it: the report/bins matrix and the vendors/customers
    list both become a `table` (dynamic bucket columns are real columns here,
    not padded markdown cells), a totals dict becomes its own `kpi_grid`
    rather than a second inline table, and the flat-dict fallback is a
    `kpi_grid` since it's just {bucket: amount} pairs.
    """
    if not isinstance(data, dict):
        return [render_table_block(data)]

    if isinstance(data.get("report"), list):
        report_rows = [r for r in data["report"] if isinstance(r, dict)]
        bins = [b for b in (data.get("bins") or []) if isinstance(b, dict) and b.get("id")]
        data_rows = [r for r in report_rows if not _is_total_row(r)]
        total_row = next((r for r in report_rows if _is_total_row(r)), None)

        if not data_rows:
            return [{"type": "markdown", "text": "_No vendors currently have outstanding aged bills._"}]

        if not _is_aging_matrix_rows(data_rows):
            table = _aging_detail_table(data_rows, bins)
            blocks: List[Dict[str, Any]] = [{
                "type": "table",
                "columns": table["columns"],
                "rows": table["rows"],
                "total_rows": len(table["rows"]),
                "truncated": False,
            }]
            if table["total"]:
                blocks.append({"type": "kpi_grid", "items": [{
                    "label": "Total Outstanding", "value": format_aed(table["total"]),
                    "raw_value": table["total"], "numeric": True, "section": None,
                }]})
            return blocks

        bin_cols = [
            (b["id"], b.get("label") or b["id"])
            for b in bins
            if any(float(r.get(b["id"]) or 0) != 0 for r in data_rows)
        ]
        columns = [{"key": "contact_name", "label": "Vendor", "align": "left"}]
        columns += [{"key": bin_id, "label": label, "align": "right"} for bin_id, label in bin_cols]
        columns.append({"key": "total", "label": "Total", "align": "right"})

        rows = []
        for row in data_rows:
            row_out = {"contact_name": row.get("contact_name") or row.get("contactName") or row.get("name") or "Unknown"}
            for bin_id, _ in bin_cols:
                row_out[bin_id] = format_aed(row.get(bin_id, 0))
            row_out["total"] = format_aed(row.get("contact_total", row.get("balance_to_bcy", 0)))
            rows.append(row_out)

        blocks: List[Dict[str, Any]] = [{
            "type": "table", "columns": columns, "rows": rows,
            "total_rows": len(rows), "truncated": False,
        }]
        if total_row is not None:
            grand_total = total_row.get("contact_total", total_row.get("balance_to_bcy", 0))
            blocks.append({"type": "kpi_grid", "items": [{
                "label": "Total Outstanding", "value": format_aed(grand_total),
                "raw_value": grand_total, "numeric": True, "section": None,
            }]})
        return blocks

    contact_key = next((k for k in ("vendors", "customers", "contacts") if k in data), None)
    if contact_key is not None:
        blocks = []
        contacts = data.get(contact_key) or []
        if contacts:
            blocks.append(render_table_block(contacts))
        else:
            blocks.append({"type": "markdown", "text": f"_No {contact_key} currently have outstanding aged bills._"})

        totals = data.get("totals")
        if isinstance(totals, dict):
            items = []
            buckets = totals.get("aging_buckets")
            if isinstance(buckets, dict):
                for k, v in buckets.items():
                    items.append({"label": k, "value": format_aed(v), "raw_value": v, "numeric": True, "section": "Aging buckets"})
            total_outstanding = totals.get("total_outstanding")
            if total_outstanding is not None:
                items.append({"label": "Total Outstanding", "value": format_aed(total_outstanding),
                              "raw_value": total_outstanding, "numeric": True, "section": None})
            if items:
                blocks.append({"type": "kpi_grid", "items": items})
        return blocks

    keys = [k for k in data.keys() if k not in _ENVELOPE_KEYS]
    if not keys:
        return [{"type": "markdown", "text": "_No records found._"}]
    items = []
    for k in keys:
        v = data[k]
        if _format_period(v) is not None:
            continue
        items.append({"label": k.replace("_", " ").title(), "value": format_aed(v),
                      "raw_value": v, "numeric": True, "section": None})
    return [{"type": "kpi_grid", "items": items}]


def render_dashboard_overview_blocks(data: Any) -> List[Dict[str, Any]]:
    """Structured counterpart to `render_dashboard_overview`.

    The monthly income/expense/cashflow series is a `chart` block (phase 4)
    — a trend over time reads faster as a line than as three text columns,
    and this was the plan's own named first producer for the block type.
    """
    if not isinstance(data, dict):
        return [render_table_block(data)]

    blocks: List[Dict[str, Any]] = []

    graph = _get_any(data, "graphData", "graph_data", "graphdata")
    if isinstance(graph, dict):
        labels = graph.get("labels") or []
        income = _get_any(graph, "incomeValues", "income_values", "incomevalues", default=[]) or []
        expense = _get_any(graph, "expenseValues", "expense_values", "expensevalues", default=[]) or []
        cashflow = _get_any(graph, "cashflowValues", "cashflow_values", "cashflowvalues", default=[]) or []
        if labels:
            def _num(v: Any) -> float:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            n = len(labels)
            series = [
                {"name": "Income", "data": [_num(income[i]) if i < len(income) else 0.0 for i in range(n)]},
                {"name": "Expense", "data": [_num(expense[i]) if i < len(expense) else 0.0 for i in range(n)]},
                {"name": "Cash Flow", "data": [_num(cashflow[i]) if i < len(cashflow) else 0.0 for i in range(n)]},
            ]
            blocks.append(render_chart_block(labels, series, chart_type="line", x_label="Month"))

    totals = data.get("totals")
    if isinstance(totals, dict) and totals:
        items = [{"label": k.replace("_", " ").title(), "value": format_aed(v), "raw_value": v,
                  "numeric": True, "section": None} for k, v in totals.items()]
        blocks.append({"type": "kpi_grid", "items": items})

    date_range = _get_any(data, "dateRange", "date_range", "daterange")
    period = _format_period(date_range) if isinstance(date_range, dict) else None
    if period is None and isinstance(date_range, dict):
        start = _get_any(date_range, "start", "start_date", default="?")
        end = _get_any(date_range, "end", "end_date", default="?")
        period = f"{start} to {end}"
    if period and blocks:
        blocks[0]["period"] = period

    if not blocks:
        return [render_kpi_grid_block(data)]
    return blocks


#: Formatter name -> structured block builder, for the formatters ported so far.
#: A builder may return one block dict or a list of blocks (e.g. a table plus
#: its own totals grid) — render_blocks() below flattens either shape.
BLOCK_BUILDERS = {
    "kv_summary": render_kpi_grid_block,
    "row_table": render_table_block,
    "account_tree": render_account_tree_block,
    "financial_statement": render_financial_statement_block,
    "aging_buckets": render_aging_buckets_blocks,
    "dashboard_overview": render_dashboard_overview_blocks,
}


def render_blocks(formatter_name: str, data: Any, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Structured counterpart to `render()`. Always returns a non-empty list.

    A ported formatter gets its typed block(s); anything else falls back to a
    single markdown block carrying the exact same text `render()` would have
    produced, so the frontend always has something well-formed to render
    regardless of migration progress.
    """
    if data is None or data == [] or data == {}:
        return [{"type": "markdown", "text": "_No records found._"}]

    builder = BLOCK_BUILDERS.get(formatter_name)
    if builder is None:
        return [{"type": "markdown", "text": render(formatter_name, data, query=query)}]

    try:
        if builder is render_table_block:
            if isinstance(data, dict) and isinstance(data.get("report"), list):
                return render_aging_buckets_blocks(data)
            if query:
                from gemini_brain.utils.ranking import extract_requested_count
                return [render_table_block(data, max_rows=extract_requested_count(query))]
            result = builder(data)
            return result if isinstance(result, list) else [result]
        result = builder(data)
        return result if isinstance(result, list) else [result]
    except Exception as e:
        return [{"type": "markdown", "text": f"_Failed to format data: {e}_"}]

