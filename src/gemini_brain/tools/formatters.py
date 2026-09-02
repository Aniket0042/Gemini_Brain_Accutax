"""
formatters.py — Deterministic markdown table/card renderers for Gemini Brain tools.

Formats numbers as 'AED 1,234,567.00'.
Every tool result is narrated by an LLM before reaching the user (see
orchestrator/gemini_brain_runner.py); this module's table is exposed alongside
the narration as `table_markdown`, and is also the deterministic fallback used
if narration itself fails.
"""
from __future__ import annotations

import numbers
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
        return ", ".join(
            f"{k.replace('_', ' ').title()}: {_clean_str(v, _depth + 1)}"
            for k, v in val.items()
        )
    if isinstance(val, list):
        if not val:
            return "-"
        if _depth >= 2:
            return f"({len(val)} items)"
        shown = ", ".join(_clean_str(v, _depth + 1) for v in val[:10])
        return shown + (f" (+{len(val) - 10} more)" if len(val) > 10 else "")
    s = str(val).strip()
    return s if s else "-"


def render_kv_summary(data: Any) -> str:
    """Render key-value dictionary as a markdown summary card/table."""
    if not isinstance(data, dict):
        return render_row_table(data)

    lines = ["| Metric | Value |", "|---|---|"]
    for k, v in data.items():
        k_label = k.replace("_", " ").title()
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace(".", "", 1).replace("-", "", 1).isdigit()):
            val_str = format_aed(v)
        else:
            val_str = _clean_str(v)
        lines.append(f"| **{k_label}** | {val_str} |")
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
    if isinstance(data, dict):
        # Unwrap standard dictionary if it contains a list
        for k in ("items", "results", "invoices", "bills", "transactions", "contacts", "data"):
            if k in data and isinstance(data[k], list):
                data = data[k]
                break

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

    # Pick top headers
    keys = list(first.keys())
    # Prioritize standard friendly columns
    priority_keys = ["id", "invoice_number", "bill_number", "name", "contact_name", "category", "amount", "total", "balance", "status", "date", "due_date", "created_at"]
    ordered_keys = [k for k in priority_keys if k in keys] + [k for k in keys if k not in priority_keys]
    selected_keys = ordered_keys[:7]  # Cap width for readability

    header_labels = [k.replace("_", " ").title() for k in selected_keys]
    lines = [
        "| " + " | ".join(header_labels) + " |",
        "| " + " | ".join(["---"] * len(selected_keys)) + " |",
    ]

    for row in data[:max_rows]:
        row_vals = []
        for k in selected_keys:
            v = row.get(k)
            if "amount" in k.lower() or "balance" in k.lower() or "total" in k.lower() or "price" in k.lower():
                row_vals.append(format_aed(v))
            else:
                row_vals.append(_clean_str(v))
        lines.append("| " + " | ".join(row_vals) + " |")

    if len(data) > max_rows:
        lines.append(f"\n_Showing {max_rows} of {len(data)} total records._")

    return "\n".join(lines)


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

        data_rows = [r for r in report_rows if r.get("row_type") != "total"]
        total_row = next((r for r in report_rows if r.get("row_type") == "total"), None)

        if not data_rows:
            return "_No vendors currently have outstanding aged bills._"

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


def render(formatter_name: str, data: Any) -> str:
    """Render data into markdown using the specified formatter."""
    if data is None or data == [] or data == {}:
        return "_No records found._"
    fn = FORMATTERS.get(formatter_name, render_row_table)
    try:
        res = fn(data)
        try:
            from gemini_brain.formatting.markdown import normalize_markdown
            return normalize_markdown(res)
        except Exception:
            return res
    except Exception as e:
        return f"_Failed to format data: {e}_"

