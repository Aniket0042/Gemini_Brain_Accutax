"""Multi-org questions for figures no report computes, and open questions answered by a scorecard.

Cases come from the role-based manual test run: "gross margin" came back as net
margin, "fixed assets" as total assets, EBITDA and cash flow were made up, and
"are any of my companies in trouble" got generic advice with no data.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org_plan
from gemini_brain.orchestrator.multi_org import OrgRun, needs_rewrite, run_multi_org
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    SCORECARD_METRICS,
    build_comparison,
    build_multi_comparison,
    computed_answer,
    is_scorecard_question,
    match_metrics,
    match_series,
)
from gemini_brain.orchestrator.multi_org_plan import DATA, UNSUPPORTED, plan_query


class _NoModel:
    def _call_llm(self, *args, **kwargs):
        raise AssertionError("planned without a model call")

    @staticmethod
    def _parse_json(text, default=None):
        return default


def _plan(question: str):
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        return plan_query(question, 1, _NoModel(), 1)


# ── Figures that cannot be computed ──────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Compare gross margin",
    "Compare EBITDA",
    "Compare operating profit this year",
    "Compare working capital",
    "Compare current ratio",
    "Compare cost of sales",
    "Compare other income",
    "Income tax expense per entity",
    "Compare corporate tax liability FY 2025",
    "Compare fixed assets",
    "Compare current liabilities",
    "Cash flow comparison this year",
    "Compare DSO",
    "Which entity has the highest payroll cost?",
    "Other income by month",
])
def test_unsupported_figure_is_refused_without_fetching(question):
    plan = _plan(question)
    assert plan.kind == UNSUPPORTED
    assert plan.notes and "is not available" in plan.notes[0]


@pytest.mark.parametrize("question, wrong", [
    ("Compare gross margin", "profit_margin"),
    ("Compare fixed assets", "total_assets"),
    ("Compare current liabilities", "total_liabilities"),
    ("Compare other income", "revenue"),
    ("Compare cost of sales", "revenue"),
    ("Income tax payable per entity", "payables"),
])
def test_unsupported_words_never_select_a_neighbouring_metric(question, wrong):
    assert wrong not in [m.key for m in match_metrics(question)]


def test_other_income_by_month_is_not_a_revenue_series():
    assert match_series("Other income by month") is None


def test_supported_and_unsupported_together_fetch_the_supported_and_note_the_rest():
    plan = _plan("Compare revenue and EBITDA")
    assert plan.kind == DATA and plan.metrics == ["revenue"]
    assert plan.notes[0].startswith("EBITDA / operating profit is not available")


def test_list_question_naming_an_unsupported_term_is_not_refused():
    with patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", side_effect=RuntimeError("no model")):
        plan = _plan("List fixed asset accounts for each organization")
    assert plan is None or plan.kind != UNSUPPORTED


def test_unsupported_answer_is_written_once_with_no_fetch_or_model_call():
    class Runner(_NoModel):
        def _retrieve(self, *args, **kwargs):
            raise AssertionError("nothing should be fetched")

    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        env = run_multi_org("Compare EBITDA", [1, 2], {1: {"name": "A"}, 2: {"name": "B"}},
                            runner_factory=Runner, run_kwargs={"allowed_org_ids": [1, 2], "user_id": 1},
                            planner=plan_query)
    assert "EBITDA / operating profit is not available" in env["answer"]
    assert "Closest available: net profit." in env["answer"]
    assert env["routing_info"]["layout"] == "unsupported"
    assert env["token_usage"]["llm_calls"] == 0


def test_note_is_added_after_the_computed_answer():
    class Runner(_NoModel):
        def _enforce_tenant_isolation(self, **kwargs):
            return kwargs["organization_id"]

        def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
            from gemini_brain.resilience.outcomes import Outcome, Retrieved
            return Retrieved(Outcome.OK, payload={"summary": {"total_income": 10.0 * organization_id}},
                             tier="sql_report", endpoint=sel["endpoint"])

    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        env = run_multi_org("Compare revenue and EBITDA", [1, 2],
                            {1: {"name": "A", "currency": "AED"}, 2: {"name": "B", "currency": "AED"}},
                            runner_factory=Runner, run_kwargs={"allowed_org_ids": [1, 2], "user_id": 1},
                            planner=plan_query)
    assert env["answer"].index("Highest: B") < env["answer"].index("EBITDA / operating profit is not available")


# ── Wording that used to miss or mismatch ────────────────────────────────────

@pytest.mark.parametrize("question, keys", [
    ("Input VAT vs output VAT per entity this year", ["input_vat", "output_vat"]),
    ("Which org made a loss this year?", ["net_profit"]),
    ("What is the combined cash position across all entities?", ["cash_balance"]),
    ("Compare revenue this year vs last year for each org", ["revenue_growth"]),
    ("Which business should I invest more in based on growth and margin?", ["revenue_growth", "profit_margin"]),
    ("Compare growth", ["revenue_growth"]),
    ("Compare VAT payable for Q2 2026", ["vat_payable"]),
])
def test_wording_maps_to_the_figure_asked_for(question, keys):
    assert [m.key for m in match_metrics(question)] == keys


def test_vs_previous_quarter_compares_with_the_previous_period():
    plan = _plan("Compare expenses this quarter vs previous quarter")
    assert plan.metrics == ["expense_growth"]
    assert plan.selection["query_params"]["basis"] == "period"


def test_definition_question_is_not_rewritten_into_a_data_question():
    assert needs_rewrite("What is VAT?") is False


# ── Open questions get a scorecard ───────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "Which of my companies is performing best overall?",
    "Are any of my companies in trouble?",
    "Summarize each company in 3 bullets",
    "How are we doing this year?",
    "Which entity is the weakest?",
    "Give me an overview of the group",
])
def test_open_question_is_a_scorecard(question):
    assert is_scorecard_question(question)
    plan = _plan(question)
    assert (plan.kind, plan.source, plan.metrics) == (DATA, "scorecard", list(SCORECARD_METRICS))
    assert needs_rewrite(question) is False


@pytest.mark.parametrize("question", [
    "How do I set up a company profile?",
    "What is the best way to record my invoices?",
    "Which organization has the best profit margin?",
    "List the top customers for each company",
    "Compare EBITDA for each company",
])
def test_how_to_metric_and_list_questions_are_not_scorecards(question):
    assert not is_scorecard_question(question)


def _run(oid: int, **summary) -> OrgRun:
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "results": [{"summary": summary}]})


def test_scorecard_answer_flags_warning_signs_from_the_table():
    metrics = [BY_KEY[k] for k in ("net_profit", "cash_balance", "total_equity")]
    runs = [_run(1, net_profit=500.0, total_balance=100.0, total_equity=900.0),
            _run(2, net_profit=-12690.0, total_balance=-13652073.0, total_equity=-6697591.0)]
    answer = computed_answer(build_multi_comparison(metrics, runs, "are any of my companies in trouble", {}))
    assert "Warning signs: Org2 (net loss, negative cash, negative equity)." in answer
    assert "Org1 (" not in answer


def test_revenue_decline_is_a_warning_sign():
    metrics = [BY_KEY["revenue"], BY_KEY["revenue_growth"]]
    runs = [_run(1, total_income=100.0, growth_pct=-12.5), _run(2, total_income=50.0, growth_pct=4.0)]
    answer = computed_answer(build_multi_comparison(metrics, runs, "how are we doing", {}))
    assert "Warning signs: Org1 (revenue down 12.50%)." in answer


def test_no_warning_line_when_nothing_is_negative():
    runs = [_run(1, net_profit=5.0), _run(2, net_profit=1.0)]
    assert "Warning signs" not in computed_answer(build_comparison(BY_KEY["net_profit"], runs, "profit"))
