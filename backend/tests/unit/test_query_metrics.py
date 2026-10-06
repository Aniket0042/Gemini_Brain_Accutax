"""query_metrics: tool input validation and query building against a fixed catalog."""
import datetime as dt
import time
from decimal import Decimal

import pytest

from gemini_brain.semantic import cube_client, query_metrics
from gemini_brain.semantic.catalog import ToolInputError, parse_meta

META = {"cubes": [
    {"name": "pnl", "type": "view", "description": "Profit and loss.",
     "meta": {"kind": "flow", "time_dimension": "pnl.transaction_date", "ai_context": "Revenue and profit."},
     "measures": [{"name": "pnl.revenue", "description": "Revenue"},
                  {"name": "pnl.net_profit", "description": "Net profit"},
                  {"name": "pnl.net_margin_pct", "description": "Net margin"}],
     "dimensions": [{"name": "pnl.organization_id"}, {"name": "pnl.organization_name"}, {"name": "pnl.currency"},
                    {"name": "pnl.transaction_date"}, {"name": "pnl.account_name"}]},
    {"name": "balance_sheet", "type": "view", "description": "Balances.",
     "meta": {"kind": "balance", "time_dimension": "balance_sheet.transaction_date",
              "always_group_by": ["balance_sheet.account_currency"]},
     "measures": [{"name": "balance_sheet.cash_and_bank"}],
     "dimensions": [{"name": "balance_sheet.organization_id"}, {"name": "balance_sheet.organization_name"},
                    {"name": "balance_sheet.currency"}, {"name": "balance_sheet.transaction_date"},
                    {"name": "balance_sheet.account_currency"}]},
    {"name": "receivables", "type": "view", "description": "Open invoices.", "meta": {"kind": "current"},
     "measures": [{"name": "receivables.overdue_amount"}],
     "dimensions": [{"name": "receivables.organization_id"}, {"name": "receivables.organization_name"},
                    {"name": "receivables.currency"}, {"name": "receivables.aging_bucket"}]},
    {"name": "gl_lines", "type": "cube", "measures": [], "dimensions": []},
    {"name": "broken", "type": "view", "meta": {"kind": "flow"}, "measures": [], "dimensions": []},
]}
CATALOG = parse_meta(META)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(query_metrics.periods, "today_in", lambda tz: dt.date(2026, 10, 6))


def test_catalog_keeps_views_only_and_skips_bad_meta():
    assert sorted(CATALOG.views) == ["balance_sheet", "pnl", "receivables"]
    assert "pnl.organization_id" not in CATALOG.views["pnl"].dimensions


def test_demo_01_profit_margin_above_20_percent():
    query, view, note = query_metrics.build_query({
        "view": "pnl",
        "measures": ["pnl.net_margin_pct", "pnl.revenue", "pnl.net_profit"],
        "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": [20]}],
    }, CATALOG)
    assert query == {
        "measures": ["pnl.net_margin_pct", "pnl.revenue", "pnl.net_profit"],
        "dimensions": ["pnl.organization_id", "pnl.organization_name", "pnl.currency"],
        "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": ["20"]}],
        "limit": 50,
        "timeDimensions": [{"dimension": "pnl.transaction_date", "dateRange": ["2026-01-01", "2026-10-06"]}],
    }
    assert note == "Period: 2026-01-01 to 2026-10-06."


def test_balance_view_uses_as_of_and_groups_by_account_currency():
    query, _, note = query_metrics.build_query(
        {"view": "balance_sheet", "measures": ["balance_sheet.cash_and_bank"], "as_of": "2026-06-30"}, CATALOG)
    assert query["timeDimensions"] == [{"dimension": "balance_sheet.transaction_date",
                                        "dateRange": ["1900-01-01", "2026-06-30"]}]
    assert "balance_sheet.account_currency" in query["dimensions"]
    assert note == "Balances as of 2026-06-30."


def test_current_view_refuses_a_past_date():
    with pytest.raises(ToolInputError, match="current status only"):
        query_metrics.build_query({"view": "receivables", "measures": ["receivables.overdue_amount"],
                                   "period": {"preset": "last_year"}}, CATALOG)


@pytest.mark.parametrize("params,message", [
    ({"view": "gl_lines", "measures": ["gl_lines.revenue"]}, "Unknown view"),
    ({"view": "pnl", "measures": []}, "between 1 and"),
    ({"view": "pnl", "measures": ["pnl.ebitda"]}, "Unknown measure"),
    ({"view": "pnl", "measures": ["pnl.revenue"], "group_by": ["pnl.organization_id"]}, "Unknown dimension"),
    ({"view": "pnl", "measures": ["pnl.revenue"],
      "filters": [{"member": "pnl.organization_id", "operator": "equals", "values": ["99"]}]}, "Unknown filter member"),
    ({"view": "pnl", "measures": ["pnl.revenue"],
      "filters": [{"member": "pnl.revenue", "operator": "sql", "values": ["1"]}]}, "Unsupported operator"),
    ({"view": "pnl", "measures": ["pnl.revenue"], "filters": [{"member": "pnl.revenue", "operator": "gt"}]},
     "needs values"),
    ({"view": "pnl", "measures": ["pnl.revenue"], "period": {"preset": "fiscal_year"}}, "unknown period"),
    ({"view": "pnl", "measures": ["pnl.revenue"], "order": {"member": "pnl.account_name"}}, "order.member"),
    ({"view": "pnl", "measures": "pnl.revenue"}, "must be a list"),
])
def test_bad_tool_input_is_returned_to_the_model(params, message):
    with pytest.raises(ToolInputError, match=message):
        query_metrics.build_query(params, CATALOG)


def test_limit_is_capped():
    query, _, _ = query_metrics.build_query({"view": "pnl", "measures": ["pnl.revenue"], "limit": 10_000}, CATALOG)
    assert query["limit"] == query_metrics.MAX_LIMIT


def test_run_adds_notes_and_strings_for_decimals(monkeypatch):
    monkeypatch.setattr(query_metrics, "get_catalog", lambda *a, **k: CATALOG)
    seen = {}

    def fake_load(query, *, organization_ids, subject, deadline):
        seen.update(query=query, orgs=organization_ids)
        return cube_client.CubeResult(rows=[{"pnl.organization_id": Decimal("24"),
                                             "pnl.net_margin_pct": Decimal("100.0")}],
                                      query=query, elapsed_ms=5, last_refresh_time="t")

    monkeypatch.setattr(query_metrics.cube_client, "load", fake_load)
    out = query_metrics.run(
        {"view": "pnl", "measures": ["pnl.net_margin_pct"],
         "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": ["20"]}]},
        organization_ids=[24, 25], subject="u", deadline=time.monotonic() + 5)
    assert seen["orgs"] == [24, 25]
    assert out["rows"] == [{"pnl.organization_id": "24", "pnl.net_margin_pct": "100.0"}]
    assert query_metrics.MARGIN_NOTE in out["notes"] and query_metrics.CURRENCY_NOTE in out["notes"]


def test_tool_spec_lists_only_catalog_views():
    spec = query_metrics.tool_spec(CATALOG)["toolSpec"]
    assert spec["inputSchema"]["json"]["properties"]["view"]["enum"] == ["balance_sheet", "pnl", "receivables"]
    assert "measure pnl.revenue" in spec["description"]
