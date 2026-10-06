"""Conditions on a figure in multi-org questions (Phase 0.4 / 0.5 of the 2026-10 review)."""
from types import SimpleNamespace

import pytest

from gemini_brain.orchestrator.multi_org_conditions import apply_conditions, has_condition, parse_conditions
from gemini_brain.orchestrator.multi_org_metrics import BY_KEY, build_comparison, computed_answer, limit_rows, match_metrics

DEMO_MARGIN = "Which of the organizations have profit margin above 20%?"
DEMO_LOSS = "List all the organizations which are making negative profit"


def _keys(question):
    return [m.key for m in match_metrics(question)]


def _conds(question):
    conditions, notes = parse_conditions(question, match_metrics(question))
    return [(c.metric, c.op, c.values) for c in conditions], notes


# ── Which figure, which condition ────────────────────────────────────────────

def test_demo_margin_question_is_a_margin_with_a_threshold():
    assert _keys(DEMO_MARGIN) == ["profit_margin"]
    assert _conds(DEMO_MARGIN) == ([("profit_margin", "gt", (20.0,))], [])


def test_demo_loss_question_is_a_metric_question_not_a_list():
    assert _keys(DEMO_LOSS) == ["net_profit"]
    assert _conds(DEMO_LOSS) == ([("net_profit", "lt", (0.0,))], [])


@pytest.mark.parametrize("question,expected", [
    ("Which organizations are loss-making this year?", [("net_profit", "lt", (0.0,))]),
    ("Show me the companies that are profitable", [("net_profit", "gt", (0.0,))]),
    ("Organizations with revenue between 1m and 5m", [("revenue", "between", (1e6, 5e6))]),
    ("Which orgs have revenue over AED 250,000?", [("revenue", "gt", (250000.0,))]),
    ("Organizations with net margin of at least 15 percent", [("profit_margin", "gte", (15.0,))]),
    ("Which organizations have cash below 50k", [("cash_balance", "lt", (50000.0,))]),
    ("Companies where expenses > 1.5 million", [("expenses", "gt", (1.5e6,))]),
])
def test_conditions_are_read(question, expected):
    assert _conds(question)[0] == expected


def test_a_number_of_days_is_a_period_not_a_threshold():
    assert not has_condition("Overdue receivables over 90 days for each organization")


@pytest.mark.parametrize("question", [
    "list invoices above 50,000",
    "Which customers have revenue above 50k across the organizations",
    "show me all bills over 10,000",
])
def test_document_and_contact_lists_stay_lists(question):
    assert match_metrics(question) == []


@pytest.mark.parametrize("question,reason", [
    ("Organizations with revenue above 20%", "is an amount, not a percentage"),
    ("Which organizations have profit margin above average", "comparisons with an average"),
])
def test_conditions_that_cannot_be_applied_are_said(question, reason):
    conditions, notes = parse_conditions(question, match_metrics(question) or [BY_KEY["revenue"]])
    assert not conditions and any(reason in n for n in notes)


# ── Applied to the computed comparison ───────────────────────────────────────

def _run(org, name, margin):
    summary = {"profit_margin_pct": margin, "total_income": 1, "total_expenses": 1, "net_profit": margin}
    return SimpleNamespace(org_id=org, name=name, currency="AED", answered=True,
                           result={"status": "ok", "payloads": {"rpt_profit_summary": {"summary": summary}}})


RUNS = [_run(24, "Org1", 100.0), _run(25, "Org2", 70.37), _run(32, "Org9", -888.89), _run(26, "Org3", None)]


def test_only_matching_organizations_remain_and_the_answer_says_so():
    comparison = build_comparison(BY_KEY["profit_margin"], RUNS, DEMO_MARGIN)
    filtered = limit_rows(apply_conditions(comparison, DEMO_MARGIN), DEMO_MARGIN)
    assert [r["organization"] for r in filtered["rows"]] == ["Org1", "Org2"]
    assert filtered["condition"] == {"text": "Profit margin above 20%", "matched": 2, "of": 3}
    answer = computed_answer(filtered)
    assert "2 of 3 organizations with profit margin above 20%" in answer
    assert "Org9" not in answer.split("\n")[0]
    assert "cannot meet the condition" in answer  # Org3 has no margin and is listed separately


def test_no_match_is_stated_not_hidden():
    question = "Which organizations have profit margin above 500%?"
    filtered = apply_conditions(build_comparison(BY_KEY["profit_margin"], RUNS, question), question)
    assert filtered["rows"] == []
    assert "No organization has profit margin above 500%." in computed_answer(filtered)


def test_without_a_condition_nothing_changes():
    question = "Compare profit margin across organizations"
    comparison = build_comparison(BY_KEY["profit_margin"], RUNS, question)
    assert apply_conditions(comparison, question) is comparison


def test_unapplied_condition_is_noted_in_the_answer():
    question = "Which organizations have profit margin above average"
    comparison = build_comparison(BY_KEY["profit_margin"], RUNS, question)
    answer = computed_answer(apply_conditions(comparison, question))
    assert "was not applied" in answer and "Org9" in answer  # everything shown, and said why


@pytest.mark.parametrize("question,overlap", [
    ("Which vendors do the organizations share?", True),
    ("Which customers do they share?", True),
    ("Vendors shared between the organizations", True),
    ("Compare share capital across organizations", False),
    ("What is our market share?", False),
])
def test_share_means_overlap_only_with_contacts(question, overlap):
    from gemini_brain.orchestrator.multi_org_present import OVERLAP, choose_shape

    assert (choose_shape(question) == OVERLAP) is overlap
