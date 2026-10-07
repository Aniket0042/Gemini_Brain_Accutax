"""list_documents and query_metrics trends: query building against a fixed catalog."""
import datetime as dt

import pytest

from gemini_brain.semantic import list_documents, query_metrics
from gemini_brain.semantic.catalog import ToolInputError, parse_meta

_AUTO = ["organization_id", "organization_name", "currency"]


def _view(name, kind, measures, dims, time_dimension=None):
    meta = {"kind": kind, **({"time_dimension": time_dimension} if time_dimension else {})}
    return {"name": name, "type": "view", "meta": meta,
            "measures": [{"name": f"{name}.{m}"} for m in measures],
            "dimensions": [{"name": f"{name}.{d}"} for d in _AUTO + dims]}


CATALOG = parse_meta({"cubes": [
    _view("sales", "flow", ["net_sales", "output_vat", "invoice_count"],
          ["document_number", "document_date", "document_weekday", "document_type", "status", "customer_name"],
          "sales.document_date"),
    _view("purchases", "flow", ["net_purchases", "input_vat"],
          ["document_number", "document_date", "document_weekday", "document_type", "status", "vendor_name"],
          "purchases.document_date"),
    _view("receivables", "current", ["outstanding", "overdue_amount"],
          ["document_number", "document_date", "due_date", "days_overdue", "aging_bucket", "status", "customer_name"]),
    _view("pnl", "flow", ["revenue", "net_profit"], ["transaction_date"], "pnl.transaction_date"),
    _view("balance_sheet", "balance", ["cash_and_bank"], ["transaction_date"], "balance_sheet.transaction_date"),
]})


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(query_metrics.periods, "today_in", lambda tz: dt.date(2026, 10, 7))


def test_top_bills_are_one_row_per_document_largest_first():
    query, note = list_documents.build_query({"type": "bills", "limit": 10}, CATALOG)
    assert query["measures"] == ["purchases.net_purchases", "purchases.input_vat"]
    assert query["dimensions"][:3] == ["purchases.organization_id", "purchases.organization_name", "purchases.currency"]
    assert "purchases.document_number" in query["dimensions"] and "purchases.vendor_name" in query["dimensions"]
    assert query["order"] == {"purchases.net_purchases": "desc"}
    assert query["limit"] == 10
    assert query["timeDimensions"][0]["dateRange"] == ["2026-01-01", "2026-10-07"]
    assert note.startswith("Period: 2026-01-01")


def test_weekend_invoices_filter_on_the_weekday():
    query, _ = list_documents.build_query({
        "type": "sales_invoices", "period": {"preset": "last_year"},
        "filters": [{"member": "sales.document_weekday", "operator": "equals", "values": ["Saturday", "Sunday"]}],
    }, CATALOG)
    assert query["filters"] == [{"member": "sales.document_weekday", "operator": "equals", "values": ["Saturday", "Sunday"]}]
    assert query["timeDimensions"][0]["dateRange"] == ["2025-01-01", "2025-12-31"]


def test_open_receivables_have_no_period_and_order_by_days_overdue():
    query, note = list_documents.build_query({
        "type": "open_receivables", "order": {"by": "days_overdue"},
        "filters": [{"member": "receivables.days_overdue", "operator": "gt", "values": [180]}],
    }, CATALOG)
    assert "timeDimensions" not in query
    assert query["order"] == {"receivables.days_overdue": "desc"}
    assert query["filters"][0]["values"] == ["180"]
    assert note.startswith("Current status on 2026-10-07")


@pytest.mark.parametrize("params,message", [
    ({"type": "payslips"}, "type must be one of"),
    ({"type": "bills", "order": {"by": "vendor"}}, "order.by"),
    ({"type": "open_receivables", "period": {"preset": "ytd"}}, "current status only"),
    ({"type": "bills", "filters": [{"member": "pnl.revenue", "operator": "gt", "values": [1]}]}, "Unknown filter member"),
    ({"type": "bills", "filters": [{"member": "purchases.organization_id", "operator": "equals", "values": [1]}]},
     "Unknown filter member"),
])
def test_bad_input_goes_back_to_the_model(params, message):
    with pytest.raises(ToolInputError, match=message):
        list_documents.build_query(params, CATALOG)


def test_limit_is_capped():
    query, _ = list_documents.build_query({"type": "sales_invoices", "limit": 5000}, CATALOG)
    assert query["limit"] == list_documents.MAX_LIMIT


def test_trend_adds_a_granularity_and_allows_more_rows():
    query, _, note = query_metrics.build_query(
        {"view": "pnl", "measures": ["pnl.revenue"], "granularity": "month", "limit": 250}, CATALOG)
    assert query["timeDimensions"] == [{"dimension": "pnl.transaction_date",
                                        "dateRange": ["2026-01-01", "2026-10-07"], "granularity": "month"}]
    assert query["limit"] == 250
    assert note.endswith("by month.")


def test_a_trend_without_a_limit_gets_the_series_cap():
    query, _, _ = query_metrics.build_query({"view": "pnl", "measures": ["pnl.revenue"], "granularity": "month"}, CATALOG)
    assert query["limit"] == query_metrics.MAX_SERIES_LIMIT


def test_without_a_trend_the_row_cap_stays():
    query, _, _ = query_metrics.build_query({"view": "pnl", "measures": ["pnl.revenue"], "limit": 250}, CATALOG)
    assert query["limit"] == query_metrics.MAX_LIMIT


@pytest.mark.parametrize("params", [
    {"view": "balance_sheet", "measures": ["balance_sheet.cash_and_bank"], "granularity": "month"},
    {"view": "pnl", "measures": ["pnl.revenue"], "granularity": "hour"},
])
def test_granularity_only_on_period_views(params):
    with pytest.raises(ToolInputError, match="granularity"):
        query_metrics.build_query(params, CATALOG)


def test_tool_spec_lists_document_types_and_filter_members():
    spec = list_documents.tool_spec(CATALOG)["toolSpec"]
    assert spec["name"] == "list_documents"
    assert spec["inputSchema"]["json"]["properties"]["type"]["enum"] == sorted(list_documents.DOCUMENT_TYPES)
    assert "sales.document_weekday" in spec["description"]


# ── Figures worked out in code ───────────────────────────────────────────────

def _row(org, period, revenue, margin="10.0", currency="AED"):
    return {"pnl.organization_id": org, "pnl.organization_name": f"Org {org}", "pnl.currency": currency,
            "pnl.transaction_date.month": f"{period}T00:00:00.000", "pnl.revenue": revenue, "pnl.net_margin_pct": margin}


def test_totals_add_amounts_but_never_percentages():
    rows = [_row(24, "2026-01-01", "765692"), _row(25, "2026-01-01", "-13652073"), _row(26, "2026-01-01", None)]
    query = {"measures": ["pnl.revenue", "pnl.net_margin_pct"]}
    out = query_metrics.summarize(rows, query, CATALOG.views["pnl"], None)
    assert out == {"totals": {"currency": "AED", "pnl.revenue": "-12886381"}}


def test_no_total_across_currencies():
    rows = [_row(24, "2026-01-01", "100"), _row(25, "2026-01-01", "100", currency="USD")]
    assert "totals" not in query_metrics.summarize(rows, {"measures": ["pnl.revenue"]}, CATALOG.views["pnl"], None)


def test_a_trend_gets_each_organizations_total_best_and_worst_period():
    rows = [_row(24, "2026-01-01", "900099"), _row(24, "2026-02-01", "-24237"), _row(24, "2026-03-01", "1528362"),
            _row(25, "2026-01-01", "5")]
    out = query_metrics.summarize(rows, {"measures": ["pnl.revenue"]}, CATALOG.views["pnl"], "month")
    org = out["per_organization"]["Org 24"]["pnl.revenue"]
    assert org == {"total": "2404224", "periods_with_data": 3, "best_period": "2026-03-01", "best": "1528362",
                   "worst_period": "2026-02-01", "worst": "-24237"}


def test_no_totals_from_a_capped_result():
    rows = [_row(24, "2026-01-01", "100"), _row(25, "2026-01-01", "200")]
    assert query_metrics.summarize(rows, {"measures": ["pnl.revenue"], "limit": 2}, CATALOG.views["pnl"], None) == {}
    assert query_metrics.summarize(rows, {"measures": ["pnl.revenue"], "limit": 3}, CATALOG.views["pnl"], None)["totals"]
