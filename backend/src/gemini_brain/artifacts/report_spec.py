"""Normalize any retrieved payload into one ReportSpec for canvas + files."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.tools.formatters import (
    _format_metric_display,
    _format_period,
    _get_any,
    render_table_block,
)

CANVAS_MAX_ROWS = 50
XLSX_MAX_ROWS = 10_000
MAX_CHART_CATEGORIES = 12
MAX_CANVAS_COLUMNS = 8
MAX_PIE_SLICES = 8

PASTEL_COLORS = [
    "#8EC5D6",
    "#F4B183",
    "#C3B1E1",
    "#9DD9C5",
    "#F3A6B8",
    "#F2D98A",
    "#8FB8E8",
    "#B5D99C",
    "#E8A0C2",
    "#D4C1EC",
]

LIST_KEYS = (
    "items", "results", "invoices", "bills", "transactions", "contacts",
    "data", "rows", "records", "vendors", "customers",
)
_ENVELOPE_KEYS = frozenset({"code", "message", "success", "status"})
_DATE_KEY_HINTS = ("date", "month", "period", "year", "week", "label")
_LABEL_KEY_HINTS = (
    "name", "contact_name", "customer", "vendor", "category", "account",
    "label", "month", "date", "invoice_number", "bill_number",
)
_AMOUNT_KEY_HINTS = (
    "amount", "total", "balance", "income", "expense", "revenue",
    "value", "count", "cashflow", "tax",
)


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        cleaned = (
            v.replace(",", "")
            .replace("AED", "")
            .replace("INR", "")
            .replace("USD", "")
            .replace("£", "")
            .replace("€", "")
            .replace("₹", "")
            .replace("$", "")
            .replace("%", "")
            .strip()
        )
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _looks_date_label(key: str, samples: Sequence[str]) -> bool:
    k = (key or "").lower()
    if any(h in k for h in _DATE_KEY_HINTS):
        return True
    if not samples:
        return False
    dateish = 0
    for s in samples[:8]:
        t = str(s)
        if any(ch.isdigit() for ch in t) and ("-" in t or "/" in t or " " in t):
            dateish += 1
    return dateish >= max(1, min(3, len(samples[:8]) // 2))


def _pick_label_key(columns: List[str]) -> Optional[str]:
    lower = {c.lower(): c for c in columns}
    for hint in _LABEL_KEY_HINTS:
        if hint in lower:
            return lower[hint]
    return columns[0] if columns else None


def _pick_numeric_keys(columns: List[str], rows: List[Dict[str, Any]], label_key: Optional[str]) -> List[str]:
    ranked: List[str] = []
    for hint in _AMOUNT_KEY_HINTS:
        for c in columns:
            if c == label_key or c in ranked:
                continue
            if hint in c.lower():
                ranked.append(c)
    for c in columns:
        if c == label_key or c in ranked:
            continue
        nums = [_as_float(r.get(c)) for r in rows[:20]]
        if sum(v is not None for v in nums) >= max(1, len(nums) // 2):
            ranked.append(c)
    return ranked[:4]


def _top_n_with_other(categories: List[str], series: List[Dict[str, Any]], n: int) -> tuple[List[str], List[Dict[str, Any]]]:
    if len(categories) <= n:
        return categories, series
    primary = series[0]["data"] if series else []
    indexed = list(range(len(categories)))
    indexed.sort(key=lambda i: float(primary[i] if i < len(primary) and primary[i] is not None else 0), reverse=True)
    keep = indexed[: n - 1]
    rest = indexed[n - 1:]
    keep.sort()
    new_cats = [categories[i] for i in keep] + ["Other"]
    new_series = []
    for s in series:
        data = s.get("data") or []
        kept = [float(data[i]) if i < len(data) and data[i] is not None else 0.0 for i in keep]
        other = sum(float(data[i]) if i < len(data) and data[i] is not None else 0.0 for i in rest)
        new_series.append({"name": s.get("name") or "Value", "data": kept + [other]})
    return new_cats, new_series


def _pick_chart_type(query: str, categories: List[str], series: List[Dict[str, Any]], hint: Optional[str]) -> str:
    if hint == "pie" and len(series) == 1 and len(categories) <= MAX_PIE_SLICES:
        return "pie"
    if hint in ("bar", "line", "area"):
        return hint
    samples = [str(c) for c in categories[:6]]
    timeish = _looks_date_label("category", samples) or any(
        any(h in str(c).lower() for h in ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "q1", "q2", "q3", "q4"))
        for c in samples
    )
    if timeish:
        # Grouped bars match the P&L live-preview mock; a single series still reads as a trend.
        return "bar" if len(series) >= 2 else "line"
    return "bar"


def _unwrap_list(data: Any) -> Any:
    if isinstance(data, dict):
        for k in LIST_KEYS:
            if k in data and isinstance(data[k], list):
                return data[k]
    return data


def _period_from(data: Any) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    for blob in (data, summary):
        raw = blob.get("period")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        for key in ("period", "dateRange", "date_range", "daterange"):
            period = _format_period(blob.get(key))
            if period:
                return period
            nested = blob.get(key)
            if isinstance(nested, dict):
                label = nested.get("label")
                if isinstance(label, str) and label.strip():
                    return label.strip()
                start = _get_any(nested, "start", "start_date", "from", default="")
                end = _get_any(nested, "end", "end_date", "to", default="")
                if start or end:
                    return f"{start} to {end}".strip(" to")
    return None


_FS_HINTS = frozenset({
    "revenue", "expenses", "expense", "net_profit", "gross_profit",
    "total_revenue", "total_expenses", "profit_and_loss", "income",
    "cogs", "cost_of_goods", "operating_income", "margin_pct",
    "profit_margin_pct",
})
_MONTHLY_LIST_KEYS = (
    "monthly", "monthly_breakdown", "months", "by_month", "month_wise",
)
_REV_KEY_HINTS = ("revenue", "income", "sales", "total_revenue")
_EXP_KEY_HINTS = ("expense", "expenses", "cost", "total_expenses")
_NET_KEY_HINTS = ("net_profit", "net_income", "profit", "net")
_LABEL_ROW_HINTS = ("month", "name", "label", "period", "date", "account_name")


def _looks_like_financial_statement(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    keys = {str(k).lower() for k in data}
    if keys & _FS_HINTS:
        return True
    summary = data.get("summary")
    if isinstance(summary, dict) and ({str(k).lower() for k in summary} & _FS_HINTS):
        return True
    stmt = str(data.get("statement") or "").lower()
    return any(tok in stmt for tok in ("profit", "loss", "balance", "cash"))


def _title_from(data: Any, query: str) -> str:
    if isinstance(data, dict):
        report = data.get("report")
        if isinstance(report, str) and report.strip() and "_" not in report:
            return report.strip()
        stmt = str(data.get("statement") or "").lower()
        if "profit" in stmt or "loss" in stmt:
            return "Profit & Loss Statement"
        if "balance" in stmt:
            return "Balance Sheet"
        if "cash" in stmt:
            return "Cash Flow Statement"
        if _looks_like_financial_statement(data):
            return "Profit & Loss Statement"
    q = (query or "").strip()
    if q:
        return q[:80].rstrip("?.!")
    return "Accutax report"


def _pick_row_key(row: Dict[str, Any], hints: Sequence[str]) -> Optional[str]:
    lower = {str(k).lower(): k for k in row}
    for hint in hints:
        if hint in lower:
            return lower[hint]
        for lk, orig in lower.items():
            if hint in lk:
                return orig
    return None


def _line_items_from_section(section: Any) -> List[Dict[str, Any]]:
    if isinstance(section, list):
        return [x for x in section if isinstance(x, dict)]
    if not isinstance(section, dict):
        return []
    for key in ("line_items", "items", "accounts", "rows"):
        raw = section.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
    items: List[Dict[str, Any]] = []
    for k, v in section.items():
        if str(k).lower() in {"total", "amount", "net", "value", "line_items", "items", "accounts", "rows"}:
            continue
        n = _as_float(v)
        if n is None:
            continue
        items.append({"name": str(k).replace("_", " ").title(), "amount": n})
    return items


def _section_total(section: Any) -> Optional[float]:
    if isinstance(section, (int, float)) and not isinstance(section, bool):
        return float(section)
    if not isinstance(section, dict):
        return _as_float(section)
    for key in ("total", "amount", "net", "value", "total_revenue", "total_expenses"):
        n = _as_float(section.get(key))
        if n is not None:
            return n
    items = _line_items_from_section(section)
    if not items:
        return None
    return sum(
        _as_float(i.get("amount") or i.get("total") or i.get("net_amount") or i.get("value")) or 0.0
        for i in items
    )


def _monthly_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    blobs: List[Any] = [data]
    if isinstance(data.get("summary"), dict):
        blobs.append(data["summary"])
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        for key in _MONTHLY_LIST_KEYS:
            raw = blob.get(key)
            if isinstance(raw, list) and raw and isinstance(raw[0], dict):
                return [r for r in raw if isinstance(r, dict)]
    return []


def _chart_grouped_bar(title: str, categories: List[str], series: List[Dict[str, Any]]) -> Dict[str, Any]:
    chart = {
        "chart_type": "bar",
        "title": title,
        "x_label": "",
        "y_label": "",
        "categories": categories,
        "series": series,
        "caption": None,
    }
    if len(series) == 1 and len(categories) > 1:
        chart["bar_colors"] = [PASTEL_COLORS[i % len(PASTEL_COLORS)] for i in range(len(categories))]
    return chart


def _from_financial_statement(data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """KPIs, grouped-bar charts, and line-item tables from nested P&L payloads."""
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    revenue_section = data.get("revenue") if isinstance(data.get("revenue"), dict) else data.get("income")
    expense_section = data.get("expenses") if isinstance(data.get("expenses"), dict) else data.get("expense")

    rev_total = _section_total(revenue_section)
    if rev_total is None:
        rev_total = _as_float(summary.get("total_revenue") or data.get("total_revenue"))
    exp_total = _section_total(expense_section)
    if exp_total is None:
        exp_total = _as_float(summary.get("total_expenses") or data.get("total_expenses"))
    net = _as_float(
        data.get("net_profit")
        or summary.get("net_profit")
        or data.get("net_income")
        or summary.get("net_income")
    )
    if net is None and rev_total is not None and exp_total is not None:
        net = rev_total - exp_total
    margin = _as_float(data.get("margin_pct") or summary.get("profit_margin_pct") or summary.get("margin_pct"))
    if margin is None and rev_total:
        margin = round(((net or 0) / rev_total) * 100, 1)

    kpis: List[Dict[str, Any]] = []
    if rev_total is not None:
        kpis.append({
            "label": "Total Revenue",
            "value": rev_total,
            "formatted": _format_metric_display("total_revenue", rev_total)[0],
        })
    if net is not None:
        kpis.append({
            "label": "Net Profit",
            "value": net,
            "formatted": _format_metric_display("net_profit", net)[0],
        })
    if margin is not None:
        kpis.append({
            "label": "Margin",
            "value": margin,
            "formatted": f"{margin:.1f}%",
        })

    charts: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []

    monthly = _monthly_rows(data)
    if monthly:
        label_key = _pick_row_key(monthly[0], _LABEL_ROW_HINTS) or next(iter(monthly[0]), None)
        rev_key = _pick_row_key(monthly[0], _REV_KEY_HINTS)
        exp_key = _pick_row_key(monthly[0], _EXP_KEY_HINTS)
        net_key = _pick_row_key(monthly[0], _NET_KEY_HINTS)
        categories = [str(r.get(label_key) or "-") for r in monthly]
        series: List[Dict[str, Any]] = []
        if rev_key:
            series.append({"name": "Revenue", "data": [_as_float(r.get(rev_key)) or 0.0 for r in monthly]})
        if exp_key:
            series.append({"name": "Expenses", "data": [_as_float(r.get(exp_key)) or 0.0 for r in monthly]})
        if series:
            chart = _chart_grouped_bar("Monthly Revenue vs Expenses", categories, series)
            if rev_key and len(monthly) >= 2:
                first = _as_float(monthly[0].get(rev_key)) or 0.0
                last = _as_float(monthly[-1].get(rev_key)) or 0.0
                if first:
                    growth = (last - first) / first * 100
                    sign = "+" if growth >= 0 else ""
                    chart["caption"] = f"{sign}{growth:.0f}% revenue growth"
            charts.append(chart)
        cols = [{"key": "month", "label": "Month", "align": "left"}]
        raw_rows = []
        table_rows = []
        if rev_key:
            cols.append({"key": "revenue", "label": "Revenue", "align": "right"})
        if exp_key:
            cols.append({"key": "expenses", "label": "Expenses", "align": "right"})
        if net_key or (rev_key and exp_key):
            cols.append({"key": "net_profit", "label": "Net Profit", "align": "right"})
        for r in monthly:
            month = str(r.get(label_key) or "-")
            rv = _as_float(r.get(rev_key)) if rev_key else None
            ev = _as_float(r.get(exp_key)) if exp_key else None
            nv = _as_float(r.get(net_key)) if net_key else None
            if nv is None and rv is not None and ev is not None:
                nv = rv - ev
            row = {"month": month}
            raw = {"month": month}
            if rev_key:
                row["revenue"] = _format_metric_display("revenue", rv or 0)[0]
                raw["revenue"] = rv or 0
            if exp_key:
                row["expenses"] = _format_metric_display("expenses", ev or 0)[0]
                raw["expenses"] = ev or 0
            if "net_profit" in {c["key"] for c in cols}:
                row["net_profit"] = _format_metric_display("net_profit", nv or 0)[0]
                raw["net_profit"] = nv or 0
            table_rows.append(row)
            raw_rows.append(raw)
        tables.append({
            "title": "Monthly Breakdown",
            "columns": cols,
            "rows": table_rows,
            "total_rows": len(table_rows),
            "truncated": False,
            "raw_rows": raw_rows,
        })

    if not charts and rev_total is not None and exp_total is not None:
        charts.append(_chart_grouped_bar(
            "Revenue vs Expenses",
            ["Revenue", "Expenses"],
            [{"name": "Amount", "data": [rev_total, exp_total]}],
        ))

    line_rows: List[Dict[str, Any]] = []
    raw_line: List[Dict[str, Any]] = []
    for section_name, section in (("Revenue", revenue_section), ("Expenses", expense_section)):
        for item in _line_items_from_section(section):
            label = str(item.get("name") or item.get("account_name") or item.get("label") or "Item")
            amount = _as_float(item.get("amount") or item.get("total") or item.get("net_amount") or item.get("value"))
            if amount is None:
                continue
            pct = f"{(amount / rev_total * 100):.1f}%" if rev_total else "—"
            line_rows.append({
                "line_item": label,
                "amount": _format_metric_display("amount", amount)[0],
                "pct_revenue": pct,
            })
            raw_line.append({"line_item": label, "amount": amount, "pct_revenue": pct, "section": section_name})
    if not line_rows:
        results = data.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("account_name") or item.get("name") or item.get("account_code") or "Item")
                amount = _as_float(item.get("net_amount") or item.get("amount") or item.get("total"))
                if amount is None:
                    continue
                pct = f"{(abs(amount) / rev_total * 100):.1f}%" if rev_total else "—"
                line_rows.append({
                    "line_item": label,
                    "amount": _format_metric_display("amount", amount)[0],
                    "pct_revenue": pct,
                })
                raw_line.append({"line_item": label, "amount": amount, "pct_revenue": pct})
    if rev_total is not None:
        line_rows.insert(0, {
            "line_item": "Total Revenue",
            "amount": _format_metric_display("total_revenue", rev_total)[0],
            "pct_revenue": "100%",
        })
        raw_line.insert(0, {"line_item": "Total Revenue", "amount": rev_total, "pct_revenue": "100%"})
    if net is not None:
        line_rows.append({
            "line_item": "Net Profit",
            "amount": _format_metric_display("net_profit", net)[0],
            "pct_revenue": f"{margin:.1f}%" if margin is not None else "—",
        })
        raw_line.append({"line_item": "Net Profit", "amount": net, "pct_revenue": margin})

    if line_rows:
        tables.append({
            "title": "Line Items",
            "columns": [
                {"key": "line_item", "label": "Line Item", "align": "left"},
                {"key": "amount", "label": "Amount", "align": "right"},
                {"key": "pct_revenue", "label": "% of Revenue", "align": "right"},
            ],
            "rows": line_rows[:CANVAS_MAX_ROWS],
            "total_rows": len(line_rows),
            "truncated": len(line_rows) > CANVAS_MAX_ROWS,
            "raw_rows": raw_line[:XLSX_MAX_ROWS],
        })

    return kpis, charts, tables


def _chart_from_kpis(kpis: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    numeric = [k for k in kpis if k.get("value") is not None and "margin" not in str(k.get("label") or "").lower()]
    if len(numeric) < 2:
        return None
    return _chart_grouped_bar(
        "Key figures",
        [str(k.get("label") or "Value") for k in numeric[:8]],
        [{"name": "Amount", "data": [float(k["value"]) for k in numeric[:8]]}],
    )


def _chart_from_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Copy a formatter `chart` block into ReportSpec shape."""
    if not isinstance(block, dict):
        return None
    if block.get("categories") and block.get("series"):
        return {
            "chart_type": (block.get("chart_type") or "bar").lower(),
            "title": block.get("title") or "Chart",
            "x_label": block.get("x_label") or "",
            "y_label": block.get("y_label") or "",
            "categories": list(block["categories"]),
            "series": list(block["series"]),
        }
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    labels = data.get("labels") or block.get("labels")
    datasets = data.get("datasets") or block.get("datasets")
    if not labels or not isinstance(datasets, list) or not datasets:
        return None
    series = []
    for ds in datasets:
        if not isinstance(ds, dict):
            continue
        series.append({
            "name": ds.get("label") or ds.get("name") or "Value",
            "data": [_as_float(v) or 0.0 for v in (ds.get("data") or [])[: len(labels)]],
        })
    if not series:
        return None
    return {
        "chart_type": str(data.get("chartType") or block.get("chart_type") or "bar").lower().replace("column", "bar"),
        "title": block.get("title") or data.get("title") or "Chart",
        "x_label": block.get("x_label") or "",
        "y_label": block.get("y_label") or "",
        "categories": list(labels),
        "series": series,
    }


def _kpis_from_dict(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for k, v in data.items():
        if k in _ENVELOPE_KEYS or k.lower() in {"report", "period"}:
            continue
        if isinstance(v, dict) and k.lower() in {"summary", "totals", "metrics", "result"}:
            for sk, sv in v.items():
                if isinstance(sv, (dict, list)) or _format_period(sv) is not None:
                    continue
                display, raw, numeric = _format_metric_display(sk, sv)
                items.append({
                    "label": sk.replace("_", " ").title(),
                    "value": raw if numeric and raw is not None else None,
                    "formatted": display,
                })
            continue
        if isinstance(v, (dict, list)):
            continue
        if _format_period(v) is not None:
            continue
        display, raw, numeric = _format_metric_display(k, v)
        items.append({
            "label": k.replace("_", " ").title(),
            "value": raw if numeric and raw is not None else None,
            "formatted": display,
        })
    return items[:12]


def _chart_from_graph_data(graph: Dict[str, Any], query: str, hint: Optional[str]) -> Optional[Dict[str, Any]]:
    labels = list(graph.get("labels") or [])
    if not labels:
        return None

    def nums(key_opts: tuple[str, ...]) -> List[float]:
        arr = None
        for k in key_opts:
            if k in graph:
                arr = graph.get(k)
                break
        arr = arr or []
        out = []
        for i in range(len(labels)):
            v = _as_float(arr[i]) if i < len(arr) else 0.0
            out.append(v if v is not None else 0.0)
        return out

    series = []
    income = nums(("incomeValues", "income_values", "incomevalues", "income"))
    expense = nums(("expenseValues", "expense_values", "expensevalues", "expense"))
    cashflow = nums(("cashflowValues", "cashflow_values", "cashflowvalues", "cashflow"))
    if any(income):
        series.append({"name": "Income", "data": income})
    if any(expense):
        series.append({"name": "Expense", "data": expense})
    if any(cashflow):
        series.append({"name": "Cash Flow", "data": cashflow})
    if not series:
        # Any remaining numeric arrays keyed beside labels.
        for k, v in graph.items():
            if k == "labels" or not isinstance(v, list):
                continue
            data = [_as_float(x) or 0.0 for x in v[: len(labels)]]
            if len(data) < len(labels):
                data += [0.0] * (len(labels) - len(data))
            series.append({"name": k.replace("_", " ").title(), "data": data[: len(labels)]})
    if not series:
        return None
    cats, series = _top_n_with_other(labels, series, MAX_CHART_CATEGORIES)
    chart_type = _pick_chart_type(query, cats, series, hint)
    if chart_type == "pie" and (len(series) != 1 or len(cats) > MAX_PIE_SLICES):
        chart_type = "bar"
    return {
        "chart_type": chart_type,
        "title": "Trend",
        "x_label": "Period",
        "y_label": "AED",
        "categories": cats,
        "series": series,
    }


def _chart_from_rows(
    rows: List[Dict[str, Any]],
    query: str,
    hint: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not rows or not isinstance(rows[0], dict):
        return None
    columns = list(rows[0].keys())
    label_key = _pick_label_key(columns)
    numeric_keys = _pick_numeric_keys(columns, rows, label_key)
    if not label_key or not numeric_keys:
        return None
    categories = [str(r.get(label_key) or "-") for r in rows]
    series = []
    for nk in numeric_keys:
        series.append({
            "name": nk.replace("_", " ").title(),
            "data": [_as_float(r.get(nk)) or 0.0 for r in rows],
        })
    cap = MAX_PIE_SLICES if hint == "pie" else MAX_CHART_CATEGORIES
    categories, series = _top_n_with_other(categories, series, cap)
    chart_type = _pick_chart_type(query, categories, series, hint)
    if chart_type == "pie" and (len(series) != 1 or len(categories) > MAX_PIE_SLICES):
        chart_type = "bar"
    chart = {
        "chart_type": chart_type,
        "title": numeric_keys[0].replace("_", " ").title() if numeric_keys else "Values",
        "x_label": label_key.replace("_", " ").title(),
        "y_label": "AED",
        "categories": categories,
        "series": series,
    }
    if chart_type == "bar" and len(series) == 1 and len(categories) > 1:
        chart["bar_colors"] = [PASTEL_COLORS[i % len(PASTEL_COLORS)] for i in range(len(categories))]
    return chart


def _table_from_data(data: Any, max_rows: int, max_cols: Optional[int]) -> Optional[Dict[str, Any]]:
    unwrapped = _unwrap_list(data)
    if not isinstance(unwrapped, list) or not unwrapped:
        return None
    block = render_table_block(unwrapped, max_rows=max_rows)
    if block.get("type") != "table" or not block.get("rows"):
        return None
    columns = list(block.get("columns") or [])
    if max_cols is not None:
        columns = columns[:max_cols]
        keys = {c["key"] for c in columns}
        rows = [{k: r.get(k) for k in keys} for r in block["rows"]]
    else:
        rows = block["rows"]
    return {
        "title": "Details",
        "columns": columns,
        "rows": rows,
        "total_rows": block.get("total_rows") or len(unwrapped),
        "truncated": bool(block.get("truncated")) or (max_cols is not None and len(block.get("columns") or []) > max_cols),
        "raw_rows": unwrapped[:XLSX_MAX_ROWS],
    }


def build_report_spec(
    data: Any,
    query: str = "",
    *,
    org_name: Optional[str] = None,
    title: Optional[str] = None,
    chart_hint: Optional[str] = None,
    canvas_rows: int = CANVAS_MAX_ROWS,
) -> Dict[str, Any]:
    """Canonical report dict consumed by the live canvas and every file generator."""
    notes: List[str] = []
    kpis: List[Dict[str, Any]] = []
    charts: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    period = _period_from(data) if isinstance(data, dict) else None
    entity = None
    currency = "AED"
    if isinstance(data, dict):
        entity = data.get("entity") or data.get("organisation") or data.get("organization")
        currency = data.get("currency") or "AED"

    if data is None or data == [] or data == {}:
        return {
            "title": title or _title_from(data, query),
            "subtitle": org_name or "",
            "entity": entity or org_name or "",
            "period": period,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "currency": currency,
            "kpis": [],
            "charts": [],
            "tables": [],
            "notes": ["No records to present."],
            "insight": None,
        }

    if isinstance(data, dict):
        graph = _get_any(data, "graphData", "graph_data", "graphdata")
        if isinstance(graph, dict):
            chart = _chart_from_graph_data(graph, query, chart_hint)
            if chart:
                if len(chart.get("series") or []) >= 2 and chart_hint not in ("line", "area", "pie"):
                    chart["chart_type"] = "bar"
                    chart["title"] = "Monthly Revenue vs Expenses"
                charts.append(chart)

        if _looks_like_financial_statement(data):
            fs_kpis, fs_charts, fs_tables = _from_financial_statement(data)
            if fs_kpis:
                kpis = fs_kpis
            for ch in fs_charts:
                if ch not in charts:
                    charts.append(ch)
            tables.extend(fs_tables)
        if not kpis:
            kpis = _kpis_from_dict(data)

    if not tables:
        table = _table_from_data(data, max_rows=canvas_rows, max_cols=MAX_CANVAS_COLUMNS)
        if table:
            tables.append(table)
            if table["truncated"]:
                notes.append(
                    f"Showing {len(table['rows'])} of {table['total_rows']} rows. "
                    "Download Excel for the full extract."
                )
            if not charts:
                chart = _chart_from_rows(table.get("raw_rows") or [], query, chart_hint)
                if chart:
                    charts.append(chart)

    if isinstance(data, dict) and not kpis and not tables and not charts:
        kpis = _kpis_from_dict(data)

    if isinstance(data, list) and data and not isinstance(data[0], dict) and not tables:
        tables.append({
            "title": "Values",
            "columns": [{"key": "value", "label": "Value", "align": "left"}],
            "rows": [{"value": str(v)} for v in data[:canvas_rows]],
            "total_rows": len(data),
            "truncated": len(data) > canvas_rows,
            "raw_rows": [{"value": v} for v in data[:XLSX_MAX_ROWS]],
        })

    if not charts:
        fallback = _chart_from_kpis(kpis)
        if fallback:
            charts.append(fallback)

    return {
        "title": title or _title_from(data, query),
        "subtitle": org_name or "",
        "entity": (entity or org_name or ""),
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "currency": currency if isinstance(currency, str) else "AED",
        "kpis": kpis,
        "charts": charts,
        "tables": tables,
        "notes": notes,
        "insight": None,
    }


def spec_has_content(spec: Dict[str, Any]) -> bool:
    return bool(spec.get("kpis") or spec.get("charts") or spec.get("tables"))


def merge_chart_blocks(spec: Dict[str, Any], blocks: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Fold formatter `chart` blocks into the spec so the canvas always has series data."""
    from_blocks: List[Dict[str, Any]] = []
    seen = set()
    for block in blocks or []:
        if not isinstance(block, dict) or block.get("type") != "chart":
            continue
        chart = _chart_from_block(block)
        if not chart:
            continue
        key = (tuple(chart.get("categories") or []), tuple(s.get("name") for s in (chart.get("series") or [])))
        if key in seen:
            continue
        seen.add(key)
        from_blocks.append(chart)
    rest = []
    for chart in spec.get("charts") or []:
        key = (tuple(chart.get("categories") or []), tuple(s.get("name") for s in (chart.get("series") or [])))
        if key in seen:
            continue
        seen.add(key)
        rest.append(chart)
    spec["charts"] = from_blocks + rest
    return spec
