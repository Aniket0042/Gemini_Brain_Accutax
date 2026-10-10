"""Tests for model registry, effort tiers, auto mode and the grounding verifier."""
from gemini_brain.policy import choose_policy, list_models
from gemini_brain.policy.effort import EFFORT_ORDER, EFFORT_TIERS, escalate, resolve_effort
from gemini_brain.policy.verifier import verify_answer


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
    # sonnet-5 (only sonnet-5/gemini-2.5-pro actually support it), so a
    # simple lookup with no explicit model now resolves to sonnet-5 rather
    # than haiku-4.5.
    p = choose_policy("total revenue this year")
    assert p.effort.name == "exhaustive"
    assert p.model_key == "sonnet-5"


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


def test_renamed_sonnet_key_still_resolves():
    from gemini_brain.policy.registry import resolve_model

    assert resolve_model("sonnet-3.5").key == "sonnet-5"


def test_labels_and_prices_follow_the_model_id():
    from gemini_brain.config.constants import HAIKU45_ID, SONNET5_ID, model_label
    from gemini_brain.config.pricing import gemini_brain_cost

    assert model_label(HAIKU45_ID) == "Claude Haiku 4.5"
    assert model_label(SONNET5_ID) == "Claude Sonnet 5"
    assert model_label("anthropic.claude-3-haiku-20240307-v1:0") == "Claude Haiku 3"
    assert gemini_brain_cost(0, 0, 1_000_000, 1_000_000, HAIKU45_ID) == 6.0
    assert gemini_brain_cost(0, 0, 1_000_000, 1_000_000, SONNET5_ID) == 12.0
