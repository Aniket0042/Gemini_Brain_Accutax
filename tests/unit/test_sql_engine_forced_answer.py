"""
test_sql_engine_forced_answer.py — The loop must never ship its own planning notes.

The tool-calling loop used to assign `final_answer = text_output` on *every*
turn. On a turn that also made tool calls, that text is the model narrating what
it intends to do next — not an answer. If the loop then exited on the time
budget or the iteration cap, whatever plan-step commentary happened to be last
was returned to the user verbatim.

`is_garbage_answer` caught the blatant cases ("Let me check the..."), but a
model that writes fluent, well-formed commentary sails straight past it. The
fixture below is deliberately of that kind: it reads like prose, contains no
garbage phrase, and would have been shipped as the answer.

Recovery is also budgeted here (ENGINE_RECOVERY_BUDGET_SECONDS). It only ever
runs on a query that already spent its whole time budget, so it must not be the
reason that query runs longer still.
"""
import time

import pytest

from gemini_brain.config.constants import (
    ENGINE_RECOVERY_BUDGET_SECONDS,
    ENGINE_TIME_BUDGET_SECONDS,
)
from gemini_brain.sql_fallback import sql_engine as se
from gemini_brain.sql_fallback.answer_cleaner import is_garbage_answer


#: Mid-plan narration that the garbage filter does NOT catch — this is the text
#: the old code would have returned to the user as the final answer.
#: Deliberately over 300 characters. The post-loop recovery block already rejects
#: an answer shorter than that when 5+ rows were retrieved ("too thin for the row
#: count"), so shorter commentary would be caught by that heuristic and this test
#: would pass with or without the fix. Real planning narration from a capable
#: model is this long, and that is exactly the text that used to reach users.
MID_PLAN_COMMENTARY = (
    "To answer this properly I will first pull the chart of accounts, then join it "
    "against the journal entry lines for the reporting period, and finally aggregate "
    "the totals by account type so the revenue and expense sides can be compared "
    "directly against one another for the period you asked about. That ordering "
    "matters because the account type mapping has to be resolved before the totals "
    "can be attributed correctly, and attributing them the other way round would "
    "double-count any account that appears on both sides of the ledger."
)

DETERMINISTIC_TABLE = "DETERMINISTIC_TABLE_FALLBACK"
RECOVERED_ANSWER = "Total revenue for the period is AED 1,250,000.00 across 42 invoices."

ROWS = [{"customer": f"Customer {i}", "revenue": 1000.0 * i} for i in range(1, 8)]


def _tool_use_response(text: str):
    """A Bedrock Converse turn that emits text AND calls a tool — i.e. mid-plan."""
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [
            {"text": text},
            {"toolUse": {
                "toolUseId": "tool-1",
                "name": "finance_agent",
                "input": {"task": "top_customers", "params": {"limit": 7}},
            }},
        ]}},
    }


def _end_turn_response(text: str):
    """A Bedrock Converse turn that closes the conversation."""
    return {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": text}]}},
    }


class FakeClock:
    """Controllable stand-in for time.time() so budgets are testable."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAdapter:
    """Replays canned turns and records how many model calls were made."""

    def __init__(self, turns, clock=None, advance_by=0.0):
        self._turns = list(turns)
        self._clock = clock
        self._advance_by = advance_by
        self.calls = 0
        self.label = "FakeAdapter"

    def reset_tokens(self):
        pass

    def get_token_usage(self):
        return {"input_tokens": 0, "output_tokens": 0, "llm_calls": self.calls}

    def converse_with_tools(self, system_prompt, messages, tools, temperature=0.0, max_tokens=None):
        self.calls += 1
        if self._clock is not None and self._advance_by:
            self._clock.advance(self._advance_by)
            self._advance_by = 0.0  # only the first turn burns the budget
        if self._turns:
            return self._turns.pop(0)
        return _end_turn_response(RECOVERED_ANSWER)

    def converse(self, system_prompt, messages, temperature=0.0, max_tokens=None):
        self.calls += 1
        return RECOVERED_ANSWER


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Replace the coordinator pipeline and fast path so no DB or model is reached."""

    def handler(task, params):
        return {"success": True, "task": task, "sql": "SELECT 1", "results": ROWS, "row_count": len(ROWS)}

    def _pipeline():
        return (
            lambda org_id: "SYSTEM PROMPT",                     # _build_system_prompt
            [{"toolSpec": {"name": "finance_agent"}}],           # TOOL_DEFINITIONS
            {"finance_agent": handler},                          # AGENT_HANDLERS
            lambda x: x,                                         # _deep_serialize
            lambda x: x,                                         # _strip_sql_from_answer
            lambda q, r: DETERMINISTIC_TABLE,                    # _format_raw_results
            lambda task, params, current: "extreme",             # _infer_question_type
        )

    monkeypatch.setattr(se, "_get_coordinator_pipeline", _pipeline)
    monkeypatch.setattr(se, "try_fast_path", lambda *a, **k: None)


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(se.time, "time", c)
    return c


def test_commentary_fixture_would_have_reached_the_user():
    """Guards the premise: if this fails, the tests below prove nothing.

    The fixture has to slip past BOTH pre-existing safety nets — the garbage-phrase
    filter and the "too thin for the row count" length check — otherwise these
    tests would pass against the old code too.
    """
    assert not is_garbage_answer(MID_PLAN_COMMENTARY), (
        "fixture text is now caught by is_garbage_answer — pick different commentary "
        "so the test still exercises the final_turn_reached logic rather than the filter"
    )
    assert len(MID_PLAN_COMMENTARY) >= 300, (
        f"fixture is {len(MID_PLAN_COMMENTARY)} chars; under 300 the existing thin-answer "
        f"heuristic rejects it for a {len(ROWS)}-row result and the fix goes untested"
    )


def test_time_budget_exit_does_not_ship_mid_plan_commentary(stub_pipeline, clock):
    """Loop blows its time budget mid-plan → the commentary must not be the answer."""
    adapter = FakeAdapter(
        [_tool_use_response(MID_PLAN_COMMENTARY)],
        clock=clock,
        advance_by=ENGINE_TIME_BUDGET_SECONDS + ENGINE_RECOVERY_BUDGET_SECONDS + 5,
    )

    result = se.run("break down revenue by customer", adapter, organization_id=199)

    assert MID_PLAN_COMMENTARY not in result["answer"]
    # Recovery budget is gone too, so rows are rendered locally rather than narrated.
    assert result["answer"] == DETERMINISTIC_TABLE
    assert adapter.calls == 1, "recovery must not spend another model call past its deadline"


def test_time_budget_exit_recovers_via_model_when_budget_remains(stub_pipeline, clock):
    """Same exit, but inside the recovery window → narrate instead of raw-format."""
    adapter = FakeAdapter(
        [_tool_use_response(MID_PLAN_COMMENTARY)],
        clock=clock,
        advance_by=ENGINE_TIME_BUDGET_SECONDS + 2,  # past the loop budget, inside recovery
    )

    result = se.run("break down revenue by customer", adapter, organization_id=199)

    assert MID_PLAN_COMMENTARY not in result["answer"]
    assert result["answer"] == RECOVERED_ANSWER
    assert adapter.calls == 2, "expected exactly one recovery call"


def test_iteration_exhaustion_does_not_ship_mid_plan_commentary(stub_pipeline, clock, monkeypatch):
    """Loop runs out of iterations mid-plan → same rule applies."""
    monkeypatch.setattr(se, "ENGINE_MAX_ITERATIONS", 2)
    adapter = FakeAdapter([
        _tool_use_response(MID_PLAN_COMMENTARY),
        _tool_use_response(MID_PLAN_COMMENTARY),
    ])

    result = se.run("break down revenue by customer", adapter, organization_id=199)

    assert MID_PLAN_COMMENTARY not in result["answer"]
    assert result["answer"] == RECOVERED_ANSWER


def test_terminal_turn_answer_is_kept(stub_pipeline, clock):
    """The model closing the conversation is the one case where its text IS the answer."""
    # Must clear the pre-existing "answer too thin for the row count" heuristic
    # (>= 300 chars for 5+ rows), which is unrelated to what this test covers.
    final_text = (
        "Revenue for the period totals AED 28,000.00 across seven customers. "
        "Customer 7 contributed the largest single share at AED 7,000.00, followed by "
        "Customer 6 at AED 6,000.00 and Customer 5 at AED 5,000.00. The remaining four "
        "accounts are spread fairly evenly between AED 1,000.00 and AED 4,000.00, so no "
        "single relationship dominates the book. Concentration risk is therefore low for "
        "this period, and collections effort is best spread across the top three accounts."
    )
    assert len(final_text) >= 300
    adapter = FakeAdapter([
        _tool_use_response(MID_PLAN_COMMENTARY),
        _end_turn_response(final_text),
    ])

    result = se.run("break down revenue by customer", adapter, organization_id=199)

    assert result["answer"] == final_text
    assert MID_PLAN_COMMENTARY not in result["answer"]


def test_force_answer_past_deadline_skips_the_model(stub_pipeline):
    """Unit-level guard on the budget itself."""
    adapter = FakeAdapter([])
    answer = se._force_answer(
        adapter, "q", "sys", [], ROWS,
        lambda x: x, lambda q, r: DETERMINISTIC_TABLE,
        deadline=time.time() - 1,
    )
    assert answer == DETERMINISTIC_TABLE
    assert adapter.calls == 0


def test_graceful_no_data_past_deadline_skips_the_model():
    """The no-rows recovery path is budgeted the same way."""
    adapter = FakeAdapter([])
    answer = se._graceful_no_data_answer(
        adapter, "q", "sys", [], deadline=time.time() - 1,
    )
    assert adapter.calls == 0
    assert "could not be retrieved" in answer
