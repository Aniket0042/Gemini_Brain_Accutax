"""
test_sql_engine_cost_optimizations.py — Stage 2 latency work.

Three independent reductions in the tool-calling loop:
  1. TTL cache on finance_agent results, so a repeated task skips the database.
  2. Truncation of tool results the model has already reasoned over, so they stop
     being re-sent as input tokens on every subsequent iteration.
  3. Tool-set pruning driven by the intent the router already computed — no extra
     LLM call, unlike the reference implementation.

None of these may change the answer. The cache must not serve failures, and
truncation must not eat the aggregate figures.
"""
import pytest

from gemini_brain.config.constants import (
    ENGINE_TOOL_RESULT_TRUNCATE_CHARS,
    ENGINE_TRUNCATION_KEEP_RECENT,
)
from gemini_brain.sql_fallback import cost_optimizer as co
from gemini_brain.sql_fallback import sql_engine as se
from gemini_brain.sql_fallback.cost_optimizer import (
    compact_tool_result,
    resolve_complexity,
    select_tools,
)


ROWS = [{"customer": f"Customer {i}", "revenue": 1000.0 * i} for i in range(1, 6)]

TOOL_DEFS = [
    {"toolSpec": {"name": "finance_agent"}},
    {"toolSpec": {"name": "schema_agent"}},
    {"toolSpec": {"name": "tax_agent"}},
    {"toolSpec": {"name": "reasoning_agent"}},
]


@pytest.fixture(autouse=True)
def clear_cache():
    co._result_cache.clear()
    yield
    co._result_cache.clear()


# ── 1. Tool-set pruning ──────────────────────────────────────────────────────

@pytest.mark.parametrize("intent,question,expected", [
    (4, "what is our total revenue this year", "SIMPLE"),
    (3, "show me the profit and loss statement", "SIMPLE"),
    (4, "analyze expense growth vs income", "COMPLEX"),   # analytical marker
    (5, "forecast cash for next quarter", "COMPLEX"),     # forecast intent
    (7, "give me a business health check", "COMPLEX"),    # advice intent
    (1, "how do I create an invoice", "COMPLEX"),         # non-retrieval intent
])
def test_resolve_complexity(intent, question, expected):
    assert resolve_complexity(question, intent) == expected


def test_unknown_intent_keeps_the_full_tool_set():
    """Callers that don't pass an intent must behave exactly as before."""
    assert resolve_complexity("total revenue this year", None) == "COMPLEX"
    assert select_tools("COMPLEX", "total revenue this year", TOOL_DEFS) == TOOL_DEFS


def test_simple_questions_drop_the_schema_and_reasoning_agents():
    tools = select_tools("SIMPLE", "what is our total revenue", TOOL_DEFS)
    names = {t["toolSpec"]["name"] for t in tools}
    assert names == {"finance_agent"}


def test_tax_questions_keep_the_tax_agent_even_when_simple():
    tools = select_tools("SIMPLE", "how much VAT do we owe", TOOL_DEFS)
    names = {t["toolSpec"]["name"] for t in tools}
    assert names == {"finance_agent", "tax_agent"}


# ── 2. Result cache ──────────────────────────────────────────────────────────

class CountingAdapter:
    """Drives the loop through two identical tool calls, then closes."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = 0

    def reset_tokens(self):
        pass

    def get_token_usage(self):
        return {"input_tokens": 0, "output_tokens": 0, "llm_calls": self.calls}

    def converse_with_tools(self, system_prompt, messages, tools, temperature=0.0, max_tokens=None):
        self.calls += 1
        if self._turns:
            return self._turns.pop(0)
        return _end_turn("Revenue totals AED 15,000.00 across five customers. " * 8)

    def converse(self, system_prompt, messages, temperature=0.0, max_tokens=None):
        self.calls += 1
        return "fallback"


def _tool_turn(task="top_customers", params=None, tool_id="t1"):
    return {
        "stopReason": "tool_use",
        "output": {"message": {"content": [
            {"toolUse": {
                "toolUseId": tool_id,
                "name": "finance_agent",
                "input": {"task": task, "params": params or {"limit": 5}},
            }},
        ]}},
    }


def _end_turn(text):
    return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def engine_with_counting_handler(monkeypatch):
    """Stub the pipeline; the finance handler counts how often it really runs."""
    state = {"handler_calls": 0, "succeed": True}

    def handler(task, params):
        state["handler_calls"] += 1
        return {
            "success": state["succeed"],
            "task": task,
            "sql": "SELECT 1",
            "results": ROWS,
            "row_count": len(ROWS),
            "summary": {"total_revenue": 15000.0},
            "period": "2026",
        }

    def _pipeline():
        return (
            lambda org_id: "SYSTEM PROMPT",
            TOOL_DEFS,
            {"finance_agent": handler},
            lambda x: x,
            lambda x: x,
            lambda q, r: "RAW_TABLE",
            lambda task, params, current: "extreme",
        )

    monkeypatch.setattr(se, "_get_coordinator_pipeline", _pipeline)
    monkeypatch.setattr(se, "try_fast_path", lambda *a, **k: None)
    return state


def test_repeated_task_is_served_from_cache(engine_with_counting_handler):
    """Same task + params twice in one loop → one database round trip."""
    adapter = CountingAdapter([
        _tool_turn(tool_id="t1"),
        _tool_turn(tool_id="t2"),
    ])

    result = se.run("top customers please", adapter, organization_id=199)

    assert engine_with_counting_handler["handler_calls"] == 1, (
        "identical task should have been served from the TTL cache the second time"
    )
    sources = [t.get("source") for t in result["agent_trace"]]
    assert "cache_hit" in sources, f"cache hit not recorded in trace: {result['agent_trace']}"


def test_different_params_are_cached_separately(engine_with_counting_handler):
    """A different period/limit is a different question — must not share an entry."""
    adapter = CountingAdapter([
        _tool_turn(params={"limit": 5}, tool_id="t1"),
        _tool_turn(params={"limit": 20}, tool_id="t2"),
    ])

    se.run("top customers please", adapter, organization_id=199)

    assert engine_with_counting_handler["handler_calls"] == 2


def test_failed_results_are_not_cached(engine_with_counting_handler):
    """A failure must be retried, never served from cache."""
    engine_with_counting_handler["succeed"] = False
    adapter = CountingAdapter([
        _tool_turn(tool_id="t1"),
        _tool_turn(tool_id="t2"),
    ])

    se.run("top customers please", adapter, organization_id=199)

    assert engine_with_counting_handler["handler_calls"] == 2


def test_cache_is_scoped_per_organization(engine_with_counting_handler):
    """Tenant isolation: org 199's cached rows must never answer org 27."""
    adapter_a = CountingAdapter([_tool_turn(tool_id="t1")])
    se.run("top customers please", adapter_a, organization_id=199)
    calls_after_first = engine_with_counting_handler["handler_calls"]

    adapter_b = CountingAdapter([_tool_turn(tool_id="t1")])
    se.run("top customers please", adapter_b, organization_id=27)

    assert engine_with_counting_handler["handler_calls"] == calls_after_first + 1, (
        "a different organization_id must miss the cache"
    )


# ── 3. Context truncation ────────────────────────────────────────────────────

def _tool_result_message(text):
    return {"role": "user", "content": [{"toolResult": {"toolUseId": "x", "content": [{"text": text}]}}]}


def test_truncation_leaves_short_conversations_alone():
    messages = [{"role": "user", "content": [{"text": "q"}]}, _tool_result_message("y" * 5000)]
    assert se._truncate_stale_tool_results(messages) == 0
    assert len(messages[1]["content"][0]["toolResult"]["content"][0]["text"]) == 5000


def test_stale_tool_results_are_truncated_but_recent_ones_are_not():
    long_text = "z" * 5000
    messages = [
        {"role": "user", "content": [{"text": "question"}]},
        {"role": "assistant", "content": [{"text": "plan"}]},
        _tool_result_message(long_text),      # stale — should shrink
        {"role": "assistant", "content": [{"text": "plan 2"}]},
        _tool_result_message(long_text),      # recent — must stay intact
    ]

    truncated = se._truncate_stale_tool_results(messages)

    assert truncated == 1
    stale = messages[2]["content"][0]["toolResult"]["content"][0]["text"]
    recent = messages[-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert len(stale) == ENGINE_TOOL_RESULT_TRUNCATE_CHARS + 3  # + the "..." marker
    assert len(recent) == 5000, "the current exchange must not be truncated"


def test_truncation_keeps_the_aggregate_figures():
    """The whole point of hoisting: SUMMARY survives, row detail is what gets cut."""
    payload = compact_tool_result(
        {
            "period": "2026",
            "summary": {"total_revenue": 15000.0, "invoice_count": 42},
            "results": [{"customer": f"Customer {i}", "revenue": float(i)} for i in range(300)],
            "row_count": 300,
        },
        "top_customers",
    )
    messages = [
        {"role": "user", "content": [{"text": "q"}]},
        {"role": "assistant", "content": [{"text": "plan"}]},
        _tool_result_message(payload),
        {"role": "assistant", "content": [{"text": "plan 2"}]},
        _tool_result_message("recent"),
    ]

    se._truncate_stale_tool_results(messages)
    kept = messages[2]["content"][0]["toolResult"]["content"][0]["text"]

    assert "PERIOD: 2026" in kept
    assert "total_revenue=15000.0" in kept
    assert "invoice_count=42" in kept


def test_compact_result_puts_summary_before_rows():
    """Guards the ordering that makes truncation safe."""
    out = compact_tool_result(
        {
            "period": "2026",
            "summary": {"total_revenue": 15000.0},
            "results": ROWS,
            "row_count": len(ROWS),
        },
        "top_customers",
    )
    assert out.index("SUMMARY:") < out.index("ROWS:")
