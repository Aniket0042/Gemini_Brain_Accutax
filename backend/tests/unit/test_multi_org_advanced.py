"""Several metrics per org, ratios and growth, figures over time, balance sheet, plan log."""
from __future__ import annotations

import datetime
import json
from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org_plan
from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results, run_multi_org
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    PERCENT,
    build_comparison,
    build_multi_comparison,
    build_series,
    comparison_block,
    computed_view,
    match_metrics,
    match_series,
    metric_selection,
    metrics_selections,
)
from gemini_brain.orchestrator.multi_org_plan import plan_query
from gemini_brain.reports import definitions
from gemini_brain.resilience.outcomes import Outcome, Retrieved


# ── Matching ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, keys", [
    ("Compare revenue, expenses and cash this year", ["revenue", "expenses", "cash_balance"]),
    ("Compare the balance sheet", ["total_assets", "total_liabilities", "total_equity"]),
    ("Compare the P&L statement", ["revenue", "expenses", "net_profit", "profit_margin"]),
    ("Compare profit and loss", ["revenue", "expenses", "net_profit", "profit_margin"]),
    ("Compare profit margin", ["profit_margin"]),
    ("Which org has the best net margin?", ["profit_margin"]),
    ("Compare net profit", ["net_profit"]),
    ("Compare revenue growth vs last year", ["revenue_growth"]),
    ("Show revenue and profit growth", ["revenue_growth", "profit_growth"]),
    ("Which org grew sales fastest?", ["revenue_growth"]),
    ("Revenue growth and cash balance", ["revenue_growth", "cash_balance"]),
    ("Compare overdue invoices and payables", ["overdue_receivables", "payables"]),
])
def test_metric_matching(question, keys):
    assert [m.key for m in match_metrics(question)] == keys


def test_margin_is_no_longer_net_profit():
    assert [m.key for m in match_metrics("Compare margin")] == ["profit_margin"]
    assert BY_KEY["profit_margin"].unit == PERCENT


@pytest.mark.parametrize("question, expected", [
    ("Revenue by month for each organization", ("revenue", "month")),
    ("Quarterly profit trend", ("net_profit", "quarter")),
    ("Show monthly expenses", ("expenses", "month")),
    ("Revenue trend this year", ("revenue", "month")),
    ("Compare revenue this year", None),
    ("Profit margin by month", None),  # not a series metric
])
def test_series_matching(question, expected):
    got = match_series(question)
    assert (got[0].key, got[1]) == expected if expected else got is None


def test_growth_basis_from_question():
    yoy = metric_selection(BY_KEY["revenue_growth"], "revenue growth vs last year")["query_params"]
    qoq = metric_selection(BY_KEY["expense_growth"], "expense change vs previous quarter")["query_params"]
    assert (yoy["base"], yoy["basis"]) == ("income", "year")
    assert (qoq["base"], qoq["basis"]) == ("expenses", "period")


def test_metrics_sharing_a_report_fetch_it_once():
    sels = metrics_selections([BY_KEY["revenue"], BY_KEY["net_profit"], BY_KEY["profit_margin"]], "compare this year")
    assert [s["endpoint"] for s in sels] == ["rpt_income_total", "rpt_profit_summary"]


def test_planner_prefers_series_then_metrics_with_no_model_calls():
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "fast_route") as fast:
        series = plan_query("Revenue by month for each organization", 5, object(), 1)
        multi = plan_query("Compare revenue, expenses and cash this year", 5, object(), 1)
    fast.assert_not_called()
    assert (series.source, series.series, series.llm_calls) == ("series", {"metric": "revenue", "grain": "month"}, 0)
    assert multi.metrics == ["revenue", "expenses", "cash_balance"] and multi.metric is None
    assert len(multi.all_selections()) == 3


# ── Comparisons ──────────────────────────────────────────────────────────────

def _run(oid, name, payloads, currency="AED"):
    return OrgRun(oid, name, currency, result={"status": "ok", "payloads": payloads,
                                               "results": list(payloads.values())})


def test_multi_comparison_one_column_per_metric_ordered_by_first():
    metrics = [BY_KEY["revenue"], BY_KEY["expenses"]]
    runs = [
        _run(1, "A", {"rpt_income_total": {"summary": {"total_income": 100}},
                      "rpt_expense_total": {"summary": {"total_expenses": 40}}}),
        _run(2, "B", {"rpt_income_total": {"summary": {"total_income": 300}}}),  # expenses report failed
    ]
    comp = build_multi_comparison(metrics, runs, "compare", {})
    assert [r["organization"] for r in comp["rows"]] == ["B", "A"]
    assert comp["rows"][0]["values"] == {"revenue": 300.0, "expenses": None}
    blocks, prompt, rows = computed_view(comp)
    table = blocks[0]
    assert [c["key"] for c in table["columns"]] == ["organization", "revenue", "expenses"]
    assert table["columns"][1]["label"] == "Total revenue (AED)"
    assert blocks[1]["chart_type"] == "bar" and len(blocks[1]["series"]) == 2
    assert "Total expenses n/a" in prompt


def test_multi_comparison_no_chart_when_units_differ():
    metrics = [BY_KEY["revenue"], BY_KEY["profit_margin"]]
    runs = [_run(1, "A", {"rpt_income_total": {"summary": {"total_income": 100}},
                          "rpt_profit_summary": {"summary": {"profit_margin_pct": 12.5}}})]
    blocks, prompt, _ = computed_view(build_multi_comparison(metrics, runs, "compare", {}))
    assert [b["type"] for b in blocks] == ["table"]
    assert "Profit margin 12.50%" in prompt


def test_percent_metric_is_ranked_without_total_or_shares_even_across_currencies():
    runs = [_run(1, "A", {"rpt_profit_summary": {"summary": {"profit_margin_pct": 10.0}}}, "AED"),
            _run(2, "B", {"rpt_profit_summary": {"summary": {"profit_margin_pct": 25.0}}}, "USD")]
    comp = build_comparison(BY_KEY["profit_margin"], runs, "compare margin")
    assert comp["comparable"] is True and comp["total"] is None
    assert [r["organization"] for r in comp["rows"]] == ["B", "A"]
    assert all("share_pct" not in r for r in comp["rows"])
    assert comparison_block(comp)["columns"][2]["label"] == "Profit margin (%)"


def test_growth_shows_current_and_previous_beside_the_percentage():
    key = BY_KEY["revenue_growth"].fetch_key
    runs = [_run(1, "A", {key: {"summary": {"growth_pct": 50.0, "current": 150.0, "previous": 100.0}}})]
    comp = build_comparison(BY_KEY["revenue_growth"], runs, "revenue growth")
    block = comparison_block(comp)
    assert [c["key"] for c in block["columns"]] == ["rank", "organization", "value", "current", "previous"]
    assert block["rows"][0]["current"] == 150.0


def test_growth_without_previous_figure_is_missing_not_guessed():
    key = BY_KEY["revenue_growth"].fetch_key
    runs = [_run(1, "A", {key: {"summary": {"growth_pct": None, "current": 10.0, "previous": 0.0}}})]
    comp = build_comparison(BY_KEY["revenue_growth"], runs, "revenue growth")
    assert comp["rows"] == [] and comp["missing"][0]["reason"] == "no base figure to compute a percentage from"


def test_series_periods_by_organizations_with_totals_and_lines():
    runs = [
        _run(1, "A", {"s": {"series": [{"period": "2026-01", "value": 10}, {"period": "2026-02", "value": 20}]}}),
        _run(2, "B", {"s": {"series": [{"period": "2026-01", "value": 5}, {"period": "2026-02", "value": 0}]}}),
    ]
    series = build_series(BY_KEY["revenue"], "month", runs)
    blocks, prompt, rows = computed_view(series)
    table, chart = blocks
    assert [r["period"] for r in table["rows"]] == ["2026-01", "2026-02", "Total"]
    assert table["rows"][-1] == {"period": "Total", "org_1": 30, "org_2": 5}
    assert chart["chart_type"] == "line" and [s["name"] for s in chart["series"]] == ["A", "B"]
    assert "A (AED): total 30.00; best month 2026-02" in prompt


def test_merge_uses_the_computed_view_and_layout_kind():
    class Summary:
        def _call_llm(self, system, user_text, **kwargs):
            return "- ok", 1, 1

    runs = [_run(1, "A", {"s": {"series": [{"period": "2026-01", "value": 10}]}})]
    env = merge_org_results("revenue by month", runs, Summary(), comparison=build_series(BY_KEY["revenue"], "month", runs))
    assert env["routing_info"]["layout"] == "series"


# ── Fetching several reports per org ─────────────────────────────────────────

class MultiRunner:
    calls = []

    def _enforce_tenant_isolation(self, **kwargs):
        return kwargs["organization_id"]

    def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
        MultiRunner.calls.append(sel["endpoint"])
        summaries = {"rpt_income_total": {"total_income": 100.0 * organization_id},
                     "rpt_expense_total": {"total_expenses": 10.0 * organization_id}}
        if sel["endpoint"] not in summaries:
            return Retrieved(Outcome.UNAVAILABLE, reason="down", tier="sql_report", endpoint=sel["endpoint"])
        return Retrieved(Outcome.OK, payload={"summary": summaries[sel["endpoint"]]}, tier="sql_report",
                         endpoint=sel["endpoint"])

    def _call_llm(self, system, user_text, **kwargs):
        return "- summary", 1, 1


def test_several_reports_fetched_per_org_and_combined(monkeypatch):
    monkeypatch.setattr(multi_org_plan, "RETRY_DELAY_SECONDS", 0)
    MultiRunner.calls = []
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        env = run_multi_org("Compare revenue, expenses and cash this year", [1, 2],
                            {1: {"name": "A", "currency": "AED"}, 2: {"name": "B", "currency": "AED"}},
                            runner_factory=MultiRunner, run_kwargs={"allowed_org_ids": [1, 2], "user_id": 1},
                            planner=plan_query)
    comp = env["comparison"]
    assert comp["kind"] == "multi_metric" and env["routing_info"]["layout"] == "multi_metric"
    assert comp["rows"][0]["values"] == {"revenue": 200.0, "expenses": 20.0, "cash_balance": None}
    # The answer is written from the computed table: no model call at all.
    assert env["token_usage"]["llm_calls"] == 0
    assert "Cash balance: no figures." in env["answer"]


# ── Reports ──────────────────────────────────────────────────────────────────

def test_profit_margin_needs_income():
    with patch.object(definitions, "income_total", return_value={"summary": {"total_income": 0.0}}), \
         patch.object(definitions, "expense_total", return_value={"summary": {"total_expenses": 5.0}}):
        assert definitions.profit_summary({}, 1, "")["summary"]["profit_margin_pct"] is None
    with patch.object(definitions, "income_total", return_value={"summary": {"total_income": 200.0}}), \
         patch.object(definitions, "expense_total", return_value={"summary": {"total_expenses": 150.0}}):
        assert definitions.profit_summary({}, 1, "")["summary"]["profit_margin_pct"] == 25.0


@pytest.mark.parametrize("start, end, basis, prev", [
    ("2026-01-01", "2026-09-28", "year", ("2025-01-01", "2025-09-28")),
    ("2026-04-01", "2026-06-30", "period", ("2026-01-01", "2026-03-31")),
    ("2026-03-01", "2026-03-31", "period", ("2026-02-01", "2026-02-28")),
    ("2026-03-10", "2026-03-19", "period", ("2026-02-28", "2026-03-09")),
    ("2024-02-29", "2024-03-31", "year", ("2023-02-28", "2023-03-31")),
])
def test_previous_window(start, end, basis, prev):
    assert definitions._previous_window(start, end, basis) == prev


def test_growth_pct_and_zero_previous():
    values = iter([150.0, 100.0, 5.0, 0.0])
    with patch.object(definitions, "_period_value", side_effect=lambda *a: next(values)):
        assert definitions.period_growth({"base": "income"}, 1, "")["summary"]["growth_pct"] == 50.0
        assert definitions.period_growth({"base": "income"}, 1, "")["summary"]["growth_pct"] is None


def test_series_fills_empty_periods_and_labels_quarters():
    rows = [{"period_start": datetime.date(2026, 1, 1), "amount": 30}, {"period_start": datetime.date(2026, 7, 1), "amount": 5}]
    with patch.object(definitions, "query", return_value=rows):
        out = definitions.metric_series({"base": "income", "grain": "quarter",
                                         "start_date": "2026-01-01", "end_date": "2026-09-28"}, 1, "")
    assert out["series"] == [{"period": "2026-Q1", "value": 30.0}, {"period": "2026-Q2", "value": 0.0},
                             {"period": "2026-Q3", "value": 5.0}]


def test_balance_sheet_signs_and_earnings_close_into_equity():
    rows = [
        {"account_type": "Asset", "account_sub_type": "Current Asset", "account_name": "Bank", "debit_balance": 500},
        {"account_type": "Liability", "account_sub_type": "Current Liability", "account_name": "AP", "debit_balance": -300},
        {"account_type": "Equity", "account_sub_type": "Equity", "account_name": "Capital", "debit_balance": -100},
        {"account_type": "Revenue", "account_sub_type": "Revenue", "account_name": "Sales", "debit_balance": -250},
        {"account_type": "Expense", "account_sub_type": "Operating Expense", "account_name": "Rent", "debit_balance": 150},
    ]
    with patch.object(definitions, "query", return_value=rows):
        bs = definitions.balance_sheet({"as_of_date": "2026-09-28"}, 1, "")
    assert bs["summary"] == {"total_assets": 500.0, "total_liabilities": 300.0, "total_equity": 200.0, "difference": 0.0}
    assert {"account": "Accumulated earnings", "sub_type": "Equity", "balance": 100.0} in bs["equity"]


# ── Plan log ─────────────────────────────────────────────────────────────────

def test_plan_log_records_redacted_question_and_marks_gaps(tmp_path, monkeypatch):
    from gemini_brain.config.settings import settings
    from gemini_brain.orchestrator import multi_org_log

    monkeypatch.setattr(settings, "multi_org_plan_log", str(tmp_path / "plans.jsonl"))
    metric_plan = multi_org_plan.QueryPlan("data", selection={"endpoint": "rpt_income_total"}, source="metric",
                                           metric="revenue", metrics=["revenue"])
    llm_plan = multi_org_plan.QueryPlan("data", selection={"endpoint": "/report/x"}, source="llm")
    multi_org_log.record_plan("compare revenue for john@example.com", metric_plan,
                              {"routing_info": {"layout": "metric"}, "status": "ok",
                               "token_usage": {"llm_calls": 1, "elapsed_seconds": 2.0}}, 10)
    multi_org_log.record_plan("something new", llm_plan, {"routing_info": {"layout": "collapsed"}, "status": "ok"}, 2)
    records = multi_org_log.read_records()
    assert [r["matched"] for r in records] == [True, False]
    assert "john@example.com" not in records[0]["question"]
    assert records[0]["metrics"] == ["revenue"] and records[0]["orgs"] == 10
    assert "organization_id" not in json.dumps(records)


def test_plan_log_off_when_setting_empty(tmp_path):
    from gemini_brain.orchestrator import multi_org_log

    assert multi_org_log.log_path() is None  # conftest turns it off
    multi_org_log.record_plan("q", None, {}, 1)  # must not raise
