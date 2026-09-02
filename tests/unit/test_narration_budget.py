"""
test_narration_budget.py — Stage 3: payload-conditional narration.

Every data answer used to be capped at 120 words / 400 output tokens. That is the
right answer for "what is our total revenue" and the wrong one for "top 10
customers", where it collapses a ranking into a single sentence.

The cap is now chosen from the payload's shape. These tests pin three things:
  1. The classifier errs toward scalar — only payloads that clearly carry rows or
     groups pay the extra output tokens (and therefore the extra latency).
  2. The two prompts cannot drift apart on the accuracy rules that keep narration
     trustworthy.
  3. Neither prompt contains a brace, because the runner used to call .format()
     on one of them.
"""
import pytest

from gemini_brain.config.constants import (
    NARRATION_MAX_TOKENS_SCALAR,
    NARRATION_MAX_TOKENS_TABULAR,
    NARRATION_TABULAR_MIN_ROWS,
)
from gemini_brain.reasoning.claude_reasoner import (
    ANALYST_SYSTEM_PROMPT,
    ANALYST_SYSTEM_PROMPT_DETAILED,
    _format_payload_and_system,
    classify_payload_shape,
    narration_budget,
    reason_over_data,
)


# ── Shape classification ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name,data,expected", [
    ("single total",          {"total": 1250000.0, "currency": "AED"},                    "scalar"),
    ("one row",               [{"customer": "A", "revenue": 1.0}],                        "scalar"),
    ("two rows",              [{"c": "A"}, {"c": "B"}],                                   "scalar"),
    ("ten-row ranking",       [{"c": f"C{i}"} for i in range(10)],                        "tabular"),
    ("items envelope",        {"items": [{"x": i} for i in range(8)], "total": 8},        "tabular"),
    ("results envelope",      {"results": [{"x": i} for i in range(5)]},                  "tabular"),
    ("P&L monthly lines",     {"total_revenue": 1.0, "monthly": [{"m": 1}, {"m": 2}, {"m": 3}]}, "tabular"),
    ("aging bucket dict",     {"buckets": {"0-30": 1.0, "31-60": 2.0, "61-90": 3.0}},     "tabular"),
    ("metadata only",         {"code": "OK", "message": "fine", "status": "done"},        "scalar"),
    ("empty list",            [],                                                          "scalar"),
    ("empty dict",            {},                                                          "scalar"),
    ("none",                  None,                                                        "scalar"),
])
def test_classify_payload_shape(name, data, expected):
    assert classify_payload_shape(data) == expected, name


def test_threshold_boundary_is_respected():
    """One row below the threshold stays scalar; one row at it flips."""
    below = [{"c": i} for i in range(NARRATION_TABULAR_MIN_ROWS - 1)]
    at = [{"c": i} for i in range(NARRATION_TABULAR_MIN_ROWS)]
    assert classify_payload_shape(below) == "scalar"
    assert classify_payload_shape(at) == "tabular"


def test_string_dict_is_not_mistaken_for_a_breakdown():
    """A dict of labels is metadata, not a set of groups to enumerate."""
    assert classify_payload_shape({"a": "x", "b": "y", "c": "z", "d": "w"}) == "scalar"


def test_booleans_do_not_count_as_numeric_groups():
    """bool is a subclass of int — flags must not read as a numeric breakdown."""
    assert classify_payload_shape({"flags": {"a": True, "b": False, "c": True}}) == "scalar"


# ── Budget mapping ───────────────────────────────────────────────────────────

def test_budget_mapping():
    prompt, tokens = narration_budget("scalar")
    assert prompt is ANALYST_SYSTEM_PROMPT
    assert tokens == NARRATION_MAX_TOKENS_SCALAR

    prompt, tokens = narration_budget("tabular")
    assert prompt is ANALYST_SYSTEM_PROMPT_DETAILED
    assert tokens == NARRATION_MAX_TOKENS_TABULAR


def test_tabular_budget_is_actually_larger():
    assert NARRATION_MAX_TOKENS_TABULAR > NARRATION_MAX_TOKENS_SCALAR


# ── Prompt invariants ────────────────────────────────────────────────────────

ACCURACY_RULES = [
    "Never recompute, re-sum, re-average",
    "Never estimate or infer it",
    "[payload truncated]",
    "the vendors array",          # referenced by resilience/output_guard.py
    "AED 1,234,567.00",
    "VAT is 5%",
]


@pytest.mark.parametrize("rule", ACCURACY_RULES)
def test_both_prompts_carry_every_accuracy_rule(rule):
    """The rules that keep narration trustworthy are not negotiable at any length."""
    assert rule in ANALYST_SYSTEM_PROMPT, f"concise prompt lost: {rule}"
    assert rule in ANALYST_SYSTEM_PROMPT_DETAILED, f"detailed prompt lost: {rule}"


def test_prompts_contain_no_format_placeholders():
    """gemini_brain_runner used to call .format() on the analyst prompt.

    That call is gone, but a brace in either prompt would break any caller that
    reintroduces it — and would do so at runtime, not import time.
    """
    for name, prompt in (("concise", ANALYST_SYSTEM_PROMPT), ("detailed", ANALYST_SYSTEM_PROMPT_DETAILED)):
        assert "{" not in prompt and "}" not in prompt, f"{name} prompt contains a brace"


def test_concise_prompt_keeps_the_word_cap():
    assert "Maximum 120 words" in ANALYST_SYSTEM_PROMPT
    assert "Maximum 120 words" not in ANALYST_SYSTEM_PROMPT_DETAILED


def test_detailed_prompt_forbids_collapsing_a_breakdown():
    """This is the behaviour the whole stage exists to fix."""
    assert "Never collapse a breakdown" in ANALYST_SYSTEM_PROMPT_DETAILED


def test_detailed_prompt_still_defers_the_table():
    """The UI renders table_markdown above the answer — don't duplicate it."""
    assert "do not transcribe" in ANALYST_SYSTEM_PROMPT_DETAILED


# ── End-to-end through reason_over_data ──────────────────────────────────────

class CapturingAdapter:
    def __init__(self):
        self.max_tokens = None
        self.system_prompt = None
        self.label = "CapturingAdapter"

    def converse(self, system_prompt, messages, temperature=0.0, max_tokens=None):
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        return "narrated answer"

    def get_token_usage(self):
        return {"input_tokens": 10, "output_tokens": 20}


@pytest.mark.parametrize("data,expected_tokens,expected_prompt", [
    ({"total": 1250000.0}, NARRATION_MAX_TOKENS_SCALAR, ANALYST_SYSTEM_PROMPT),
    ([{"c": f"C{i}"} for i in range(10)], NARRATION_MAX_TOKENS_TABULAR, ANALYST_SYSTEM_PROMPT_DETAILED),
])
def test_reason_over_data_applies_the_right_budget(data, expected_tokens, expected_prompt):
    adapter = CapturingAdapter()
    answer, label, ti, to = reason_over_data(
        query="how did we do",
        data=data,
        endpoint="/report/sales-by-customer",
        selected_model_key="bedrock_haiku45",
        adapter_resolver=lambda key: adapter,
    )
    assert answer == "narrated answer"
    assert adapter.max_tokens == expected_tokens
    assert adapter.system_prompt.startswith(expected_prompt[:200])


def test_formatter_selects_the_detailed_prompt_for_tabular_payloads():
    system, user_msg = _format_payload_and_system(
        query="top customers",
        data=[{"c": f"C{i}"} for i in range(10)],
        endpoint="/report/sales-by-customer",
    )
    assert "Never collapse a breakdown" in system
    assert "top customers" in user_msg


def test_truncation_notice_still_works():
    """Regression guard on behaviour the shape change runs alongside."""
    payload = {"items": [{"i": i} for i in range(100)]}
    _system, user_msg = _format_payload_and_system(
        query="List transactions", data=payload, endpoint="/income/list",
    )
    assert "[payload truncated — 40 of 100 rows shown]" in user_msg
