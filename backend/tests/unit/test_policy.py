"""Tests for model registry, effort tiers, auto mode and the grounding verifier."""
from gemini_brain.agents.executor import assert_read_only
from gemini_brain.agents.finance_agent import _like, _lit
from gemini_brain.policy import choose_policy, list_models
from gemini_brain.policy.effort import EFFORT_ORDER, EFFORT_TIERS, escalate, resolve_effort
from gemini_brain.policy.verifier import verify_answer

import pytest


# ── SQL literal safety ────────────────────────────────────────────────

def test_lit_escapes_quotes():
    assert _lit("O'Brien") == "'O''Brien'"


def test_lit_neutralises_injection():
    """The payload survives as text but cannot terminate the literal early."""
    out = _lit("x' OR '1'='1")
    assert out.startswith("'") and out.endswith("'")
    # Every quote from the input is doubled, so no quote closes the literal
    # anywhere but at the very end.
    assert "'" not in out[1:-1].replace("''", "")


def test_lit_passes_numbers_unquoted():
    assert _lit(42) == "42"
    assert _lit(None) == "NULL"


def test_like_escapes_wildcards():
    # A user searching for a literal % must not match everything.
    assert r"\%" in _like("100%")


@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE income",
    "DO $$ BEGIN PERFORM 1; END $$",
    "SELECT pg_read_file('/etc/passwd')",
    "UPDATE income SET amount = 0",
    "CREATE TABLE evil (id int)",
    "COPY income TO PROGRAM 'sh'",
])
def test_assert_read_only_blocks(sql):
    with pytest.raises(ValueError):
        assert_read_only(sql)


def test_assert_read_only_allows_select_and_cte():
    assert_read_only("SELECT * FROM income WHERE organization_id = 27;")
    assert_read_only("WITH x AS (SELECT 1) SELECT * FROM x")


# ── Effort tiers ──────────────────────────────────────────────────────

def test_every_tier_is_a_superset_of_the_one_below():
    """Rising effort may only add verification, never remove it."""
    flags = ["verify_grounding", "cross_check_sources", "decompose", "dual_compute"]
    for lower, higher in zip(EFFORT_ORDER, EFFORT_ORDER[1:]):
        lo, hi = EFFORT_TIERS[lower], EFFORT_TIERS[higher]
        for flag in flags:
            assert getattr(hi, flag) or not getattr(lo, flag), f"{higher} dropped {flag}"
        assert hi.max_retrievals >= lo.max_retrievals


def test_effort_buys_verification_not_just_latency():
    assert not EFFORT_TIERS["quick"].verify_grounding
    assert EFFORT_TIERS["standard"].verify_grounding
    assert EFFORT_TIERS["thorough"].cross_check_sources
    assert EFFORT_TIERS["exhaustive"].dual_compute


def test_escalate_clamps_at_top():
    assert escalate("quick") == "standard"
    assert escalate("exhaustive") == "exhaustive"


def test_unknown_effort_falls_back_to_default():
    assert resolve_effort("nonsense").name == "exhaustive"
    assert resolve_effort(None).name == "exhaustive"


# ── Auto mode ─────────────────────────────────────────────────────────

def test_auto_defaults_to_exhaustive_for_simple_lookups():
    # DEFAULT_EFFORT is 'exhaustive' -- there is no more "cheap" tier for a
    # plain lookup with no escalation signals; every query runs at the
    # highest effort its chosen model supports. 'exhaustive' prefers
    # sonnet-3.5 (only sonnet-3.5/gemini-2.5-pro actually support it), so a
    # simple lookup with no explicit model now resolves to sonnet-3.5 rather
    # than haiku-4.5.
    p = choose_policy("total revenue this year")
    assert p.effort.name == "exhaustive"
    assert p.model_key == "sonnet-3.5"


def test_auto_escalates_analytical_questions():
    p = choose_policy("why did expenses grow compared to last year?")
    assert p.effort.name in ("thorough", "exhaustive")
    assert "analysis" in p.auto_reason


def test_auto_escalates_forecasts():
    p = choose_policy("forecast cash flow", intent=5)
    assert p.effort.name in ("thorough", "exhaustive")


def test_auto_escalates_on_partial_coverage():
    # The escalation heuristics still run and still clamp correctly at the
    # ceiling -- but with DEFAULT_EFFORT='exhaustive', the un-escalated
    # baseline is already at the top tier, so partial_coverage has nowhere
    # higher to escalate to. Both resolve to 'exhaustive'.
    base = choose_policy("total revenue this year")
    partial = choose_policy("total revenue this year", partial_coverage=True)
    assert base.effort.name == "exhaustive"
    assert partial.effort.name == "exhaustive"
    assert EFFORT_ORDER.index(partial.effort.name) >= EFFORT_ORDER.index(base.effort.name)


def test_explicit_effort_overrides_auto():
    p = choose_policy("why did expenses grow?", requested_effort="quick")
    assert p.effort.name == "quick"


def test_budget_constraint_clamps_effort():
    p = choose_policy("why did expenses grow year over year?", budget_constrained=True)
    assert p.effort.name == "standard"
    assert "usage cap" in p.auto_reason


def test_auto_always_explains_itself():
    assert choose_policy("total revenue").auto_reason


def test_model_never_exceeds_its_declared_efforts():
    for spec in list_models():
        p = choose_policy("anything", requested_model=spec["key"], requested_effort="exhaustive")
        assert p.effort.name in spec["efforts"]


# ── Grounding verifier ────────────────────────────────────────────────

PAYLOAD = {"rows": [{"customer": "A", "revenue": 1203455.0}, {"customer": "B", "revenue": 500000}]}


def test_verifier_accepts_figures_present_in_payload():
    r = verify_answer("A billed AED 1,203,455 and B billed AED 500,000.", PAYLOAD)
    assert r.grounded and r.checked == 2


def test_verifier_accepts_rounded_prose():
    assert verify_answer("A billed roughly AED 1.2M.", PAYLOAD).grounded


def test_verifier_accepts_totals_and_shares():
    assert verify_answer("Together they billed AED 1,703,455.", PAYLOAD).grounded
    assert verify_answer("A was 70.6% of revenue.", PAYLOAD).grounded


def test_verifier_catches_invented_figures():
    r = verify_answer("Revenue was AED 9,999,999.", PAYLOAD)
    assert not r.grounded
    assert "9,999,999" in r.unmatched


def test_verifier_catches_number_at_end_of_sentence():
    """Regression: the sentence-ending period once hid the figure entirely."""
    assert verify_answer("Revenue was AED 8,888,888.", PAYLOAD).checked == 1


def test_verifier_ignores_years():
    r = verify_answer("In 2026 revenue was AED 1,203,455.", PAYLOAD)
    assert r.grounded and r.skipped >= 1


def test_verifier_is_a_noop_on_empty_answer():
    assert verify_answer("", PAYLOAD).grounding_rate == 1.0


# ── Ageing-qualifier routing guard ────────────────────────────────────
# Regression: "show me top 5 vendors with overdue of 90 days" matched the
# top_vendors rule, which ranks by spend, and the overdue qualifier was
# silently dropped — answering a different question than the one asked.

from gemini_brain.router.rules import get_sql_fast_path_rules, redirect_for_aging


def _first_task(question):
    for pattern, task, _ in get_sql_fast_path_rules():
        if pattern.search(question):
            return task
    return None


@pytest.mark.parametrize("question,expected", [
    ("show me top 5 vendors with overdue of 90 days", "ap_aging"),
    ("top 5 vendors past due", "ap_aging"),
    ("top 10 customers with outstanding balances", "ar_aging"),
    ("top 5 customers in arrears", "ar_aging"),
])
def test_ranking_with_ageing_qualifier_redirects(question, expected):
    task = _first_task(question)
    assert task is not None, "expected a fast-path match to redirect"
    assert redirect_for_aging(task, question) == expected


@pytest.mark.parametrize("question", [
    "top 5 vendors",
    "top 10 customers",
    "sales by customer this year",
])
def test_plain_ranking_is_left_alone(question):
    task = _first_task(question)
    assert redirect_for_aging(task, question) is None


def test_non_ranking_rules_are_never_redirected():
    assert redirect_for_aging("profit_loss", "overdue profit and loss") is None
    assert redirect_for_aging("ap_aging", "overdue bills 90 days") is None
