"""One figure per organization: metric matching, in-code comparison, compact envelope."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org_plan
from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results, run_multi_org
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    build_comparison,
    comparison_block,
    comparison_prompt,
    match_metric,
    metric_selection,
)
from gemini_brain.orchestrator.multi_org_plan import plan_query
from gemini_brain.reports import definitions


# ── Matching: the questions from the screenshots ─────────────────────────────

@pytest.mark.parametrize("question, key", [
    ("Compare total revenue this year", "revenue"),
    ("Which organization has higher outstanding receivables?", "receivables"),
    ("Which organization has the highest cash balance?", "cash_balance"),
    ("Rank the organizations by total expenses", "expenses"),
    ("Which org is most profitable this year?", "net_profit"),
    ("Compare net income for last year", "net_profit"),
    ("Who has the most overdue invoices?", "overdue_receivables"),
    ("Compare overdue bills", "overdue_payables"),
    ("How much do we owe suppliers — compare payables", "payables"),
])
def test_comparable_questions_match_a_metric(question, key):
    assert match_metric(question).key == key


@pytest.mark.parametrize("question", [
    "List vendors with overdue bills in each organization",
    "List invoices for this year",
    "Show top 5 customers by sales for each organization",
    "Which vendors are good for both organizations?",
    "Compare revenue by month",
    "Compare cash flow",
    "What is VAT?",
])
def test_list_breakdown_and_other_questions_do_not_match(question):
    assert match_metric(question) is None


def test_period_metric_selection_uses_question_period():
    sel = metric_selection(BY_KEY["revenue"], "Compare total revenue this year")
    assert sel["endpoint"] == "rpt_income_total"
    assert sel["query_params"]["start_date"].endswith("-01-01")
    # "This year" is the year so far: future-dated invoices are not counted.
    from gemini_brain.router import dates
    assert sel["query_params"]["end_date"] == dates.today().isoformat()


def test_balance_metric_selection_is_as_of_today():
    sel = metric_selection(BY_KEY["receivables"], "Compare receivables")
    assert sel["endpoint"] == "rpt_receivables_outstanding"
    assert set(sel["query_params"]) == {"as_of_date"}


def test_planner_uses_metric_before_any_router_or_model():
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "fast_route") as fast:
        plan = plan_query("Which organization has the highest cash balance?", 5, object(), 1)
    fast.assert_not_called()
    assert (plan.source, plan.metric, plan.llm_calls) == ("metric", "cash_balance", 0)
    assert plan.selection["endpoint"] == "rpt_cash_balance"


# ── Comparison math ──────────────────────────────────────────────────────────

def _run(oid, name, value, currency="AED", status="ok", summary_extra=None):
    summary = {"total_income": value, **(summary_extra or {})}
    result = None if status == "failed" else {"status": status, "results": [{"summary": summary}]}
    return OrgRun(oid, name, currency, result=result)


def test_ranked_descending_with_shares_and_total():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100), _run(2, "B", 300)], "compare revenue")
    assert [(r["organization"], r["rank"], r["share_pct"]) for r in comp["rows"]] == [("B", 1, 75.0), ("A", 2, 25.0)]
    assert comp["total"] == 400 and comp["spread"] == 200 and comp["currency"] == "AED"


def test_lowest_question_ranks_ascending():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100), _run(2, "B", 300)], "which has the lowest revenue")
    assert comp["rows"][0]["organization"] == "A" and comp["order"] == "ascending"


def test_mixed_currencies_are_not_ranked_or_totalled():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100, "AED"), _run(2, "B", 300, "USD")], "compare revenue")
    assert comp["comparable"] is False and comp["total"] is None
    assert all(r["rank"] is None and "share_pct" not in r for r in comp["rows"])
    assert "different or unknown currencies" in comparison_prompt(comp)


def test_failed_and_valueless_orgs_are_listed_as_missing_not_guessed():
    runs = [_run(1, "A", 100), _run(2, "B", 0, status="failed"), _run(3, "C", None)]
    comp = build_comparison(BY_KEY["revenue"], runs, "compare revenue")
    assert [r["organization"] for r in comp["rows"]] == ["A"]
    assert {m["organization"]: m["reason"] for m in comp["missing"]} == {
        "B": "could not be retrieved", "C": "no data"}


def test_negative_total_gets_no_shares():
    runs = [OrgRun(1, "A", "AED", result={"status": "ok", "results": [{"summary": {"net_profit": -500}}]}),
            OrgRun(2, "B", "AED", result={"status": "ok", "results": [{"summary": {"net_profit": 100}}]})]
    comp = build_comparison(BY_KEY["net_profit"], runs, "compare profit")
    assert all("share_pct" not in r for r in comp["rows"])


def test_cash_uses_account_currency_from_report():
    runs = [OrgRun(1, "A", "AED", result={"status": "ok", "results": [
        {"summary": {"total_balance": 50, "account_currency": "USD"}}]})]
    comp = build_comparison(BY_KEY["cash_balance"], runs, "cash")
    assert comp["rows"][0]["currency"] == "USD"


def test_block_is_a_single_ranked_table():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100), _run(2, "B", 300)], "compare revenue")
    block = comparison_block(comp)
    assert block["type"] == "table"
    assert [c["key"] for c in block["columns"]] == ["rank", "organization", "value", "share_pct"]
    assert block["columns"][-1]["label"] == "% of total"
    assert "combined total" in block["period"]
    assert block["rows"][0]["organization"] == "B"


# ── Merge: compact envelope ──────────────────────────────────────────────────

class _Summary:
    prompts = []

    def _call_llm(self, system, user_text, **kwargs):
        _Summary.prompts.append(user_text)
        return "- B leads with 75%.", 20, 8


def test_merge_with_comparison_shows_one_table_and_no_per_org_blocks():
    runs = [_run(1, "A", 100), _run(2, "B", 300)]
    for r in runs:
        r.result["blocks"] = [{"type": "kpi_grid", "items": []}]
    comp = build_comparison(BY_KEY["revenue"], runs, "compare revenue")
    _Summary.prompts = []
    env = merge_org_results("compare revenue", runs, _Summary(), comparison=comp)
    assert [b["type"] for b in env["blocks"]] == ["table", "chart"]
    assert env["comparison"]["rows"][0]["organization"] == "B"
    assert _Summary.prompts == []  # no model call: the answer is written from the table
    assert "Highest: B, 300.00 AED." in env["answer"]
    assert "Lowest: A, 100.00 AED." in env["answer"]
    assert env["verification"]["unmatched"] == []


class MetricRunner:
    def _enforce_tenant_isolation(self, **kwargs):
        return kwargs["organization_id"]

    def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
        from gemini_brain.resilience.outcomes import Outcome, Retrieved
        return Retrieved(Outcome.OK, payload={"summary": {"total_income": organization_id * 10.0}},
                         tier="sql_report", endpoint=sel["endpoint"])

    def _call_llm(self, system, user_text, **kwargs):
        return "- Org 7 leads.", 10, 5


def test_end_to_end_metric_question_uses_no_model_call():
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        env = run_multi_org(
            "Compare total revenue this year", [5, 6, 7],
            {5: {"name": "Org 5", "currency": "AED"}, 6: {"name": "Org 6", "currency": "AED"},
             7: {"name": "Org 7", "currency": "AED"}},
            runner_factory=MetricRunner,
            run_kwargs={"allowed_org_ids": [5, 6, 7], "user_id": 1},
            planner=plan_query,
        )
    assert env["token_usage"]["llm_calls"] == 0
    assert [r["organization"] for r in env["comparison"]["rows"]] == ["Org 7", "Org 6", "Org 5"]
    assert env["agent_trace"][0]["metric"] == "revenue"


# ── Reports ──────────────────────────────────────────────────────────────────

def test_cash_balance_does_not_add_mixed_currency_accounts():
    rows = [{"currency": "AED", "account_count": 1, "balance": 100},
            {"currency": "USD", "account_count": 1, "balance": 50}]
    with patch.object(definitions, "query", return_value=rows):
        summary = definitions.cash_balance({}, 4, "")["summary"]
    assert summary["total_balance"] is None and summary["by_currency"] == {"AED": 100.0, "USD": 50.0}


def test_profit_summary_is_income_minus_expenses():
    with patch.object(definitions, "income_total", return_value={"summary": {"total_income": 900.0}}), \
         patch.object(definitions, "expense_total", return_value={"summary": {"total_expenses": 400.0}}):
        assert definitions.profit_summary({}, 4, "")["summary"]["net_profit"] == 500.0


def test_comparison_states_its_period():
    from gemini_brain.orchestrator.multi_org_metrics import period_label
    assert period_label({"start_date": "2026-01-01", "end_date": "2026-09-28"}) == "1 Jan 2026 – 28 Sep 2026"
    assert period_label({"as_of_date": "2026-09-28"}) == "as of 28 Sep 2026"
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100)], "revenue",
                            period={"start_date": "2026-01-01", "end_date": "2026-09-28"})
    assert comparison_block(comp)["title"] == "Total revenue, 1 Jan 2026 – 28 Sep 2026"
    assert "1 Jan 2026 – 28 Sep 2026" in comparison_prompt(comp)


# ── Chart ────────────────────────────────────────────────────────────────────

from gemini_brain.orchestrator.multi_org_metrics import comparison_chart


def test_chart_follows_the_ranking_order():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100), _run(2, "B", 300)], "compare revenue",
                            period={"start_date": "2026-01-01", "end_date": "2026-09-28"})
    chart = comparison_chart(comp)
    assert chart["type"] == "chart" and chart["chart_type"] == "hbar"
    assert chart["categories"] == ["B", "A"]
    assert chart["series"] == [{"name": "Total revenue (AED)", "data": [300, 100]}]
    assert chart["caption"] == "1 Jan 2026 – 28 Sep 2026"


def test_no_chart_for_mixed_currencies():
    comp = build_comparison(BY_KEY["revenue"], [_run(1, "A", 100, "AED"), _run(2, "B", 300, "USD")], "revenue")
    assert comparison_chart(comp) is None


def test_no_chart_for_one_org_or_all_zero():
    assert comparison_chart(build_comparison(BY_KEY["revenue"], [_run(1, "A", 100)], "revenue")) is None
    zeros = build_comparison(BY_KEY["revenue"], [_run(1, "A", 0), _run(2, "B", 0)], "revenue")
    assert comparison_chart(zeros) is None


# ── VAT, concept guard, shared contacts ──────────────────────────────────────

@pytest.mark.parametrize("question, key", [
    ("Compare VAT payable", "vat_payable"),
    ("Which org owes the most tax payable this quarter?", "vat_payable"),
    ("Compare accounts payable", "payables"),
])
def test_vat_is_its_own_metric_not_payables(question, key):
    assert match_metric(question).key == key


@pytest.mark.parametrize("question", ["What is VAT?", "Explain how revenue is recognised",
                                      "What does net profit mean?", "Difference between cash and profit"])
def test_concept_questions_never_match_a_metric(question):
    assert match_metric(question) is None


def test_shared_vendor_question_plans_vendor_totals_all_history():
    from gemini_brain.orchestrator.multi_org_metrics import contact_totals_selection
    sel = contact_totals_selection("Which vendors do both organizations use?")
    assert sel["endpoint"] == "rpt_vendor_totals"
    assert sel["query_params"]["start_date"] == "2000-01-01" and sel["query_params"]["limit"] == 500
    assert contact_totals_selection("Customers common to all orgs")["endpoint"] == "rpt_customer_totals"
    assert contact_totals_selection("items in common") is None


def test_planner_routes_shared_contacts_without_model_calls():
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None),          patch.object(multi_org_plan, "fast_route") as fast:
        plan = plan_query("Which vendors are good for both organizations?", 5, object(), 1)
    fast.assert_not_called()
    assert (plan.source, plan.selection["endpoint"], plan.llm_calls) == ("shared_contacts", "rpt_vendor_totals", 0)



def test_no_shares_when_any_value_is_negative():
    runs = [OrgRun(1, "A", "AED", result={"status": "ok", "results": [{"summary": {"total_balance": -50}}]}),
            OrgRun(2, "B", "AED", result={"status": "ok", "results": [{"summary": {"total_balance": 500}}]})]
    comp = build_comparison(BY_KEY["cash_balance"], runs, "cash")
    assert all("share_pct" not in r for r in comp["rows"])


def test_cash_balance_reads_the_ledger_per_account():
    rows = [{"account_name": "Petty Cash", "currency": "AED", "balance": 100},
            {"account_name": "Bank Account", "currency": "AED", "balance": -20}]
    with patch.object(definitions, "query", return_value=rows) as q:
        report = definitions.cash_balance({"as_of_date": "2026-09-28"}, 4, "")
    sql = q.call_args.args[0]
    assert "journal_entry_lines" in sql and "coa.account_type = 'Asset'" in sql
    assert report["summary"]["total_balance"] == 80.0 and report["summary"]["account_currency"] == "AED"
    assert [a["account"] for a in report["accounts"]] == ["Petty Cash", "Bank Account"]



# ── Same question, same path, whatever the wording ───────────────────────────

@pytest.mark.parametrize("question", [
    "compare total revenue this year",
    "Compare total revenue this year",
    "What is the total revenue for each organization this year?",
    "What was the total revenue of each organization in 2026?",
    "Show me the total revenue for this year",
    "How much revenue did each organization make this year?",
    "Total income this year by organization",
])
def test_revenue_wordings_all_match_the_revenue_metric(question):
    assert match_metric(question).key == "revenue"


def test_report_metric_marks_fast_router_plans():
    class Hit:
        intent = 4
        rule_name = "income_total"

        def to_selection_dict(self):
            return {"endpoint": "rpt_income_total", "path_params": {}, "query_params": {}}

    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "match_metrics", return_value=[]), \
         patch.object(multi_org_plan, "match_series_metrics", return_value=None), \
         patch.object(multi_org_plan, "fast_route", return_value=Hit()):
        plan = plan_query("some wording no rule understands", 5, object(), 1)
    assert (plan.source, plan.metric) == ("fast", "revenue")


def test_report_metric_marks_model_selected_plans():
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "match_metrics", return_value=[]), \
         patch.object(multi_org_plan, "match_series_metrics", return_value=None), \
         patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", return_value=({"type": 4}, 1, 1)), \
         patch.object(multi_org_plan, "select_endpoint",
                      return_value=({"endpoint": "rpt_cash_balance", "query_params": {}}, 1, 1)):
        class StubRunner:
            def _call_llm(self, *args, **kwargs):
                return "", 0, 0

            @staticmethod
            def _parse_json(text, default=None):
                return default

        plan = plan_query("anything", 5, StubRunner(), 1)
    assert plan.metric == "cash_balance"


def test_non_metric_report_stays_non_metric():
    from gemini_brain.orchestrator.multi_org_plan import QueryPlan, DATA, _with_report_metric
    plan = _with_report_metric(QueryPlan(DATA, selection={"endpoint": "/report/balance-sheet"}))
    assert plan.metric is None
