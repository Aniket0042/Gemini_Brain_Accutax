"""Phase 2 agent: tool scoping and the bounded loop, with Bedrock and Cube faked."""
import time

import pytest

from gemini_brain.agent import loop, tools
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.cube_client import CubeError

ORGS = [24, 25]
_REAL_LAW_ANSWER = loop.law.answer
META = {24: {"name": "Org One", "currency": "AED"}, 25: {"name": "Org Two", "currency": "AED"}}


@pytest.fixture(autouse=True)
def _no_presidio(monkeypatch):
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop.law, "answer", lambda question, messages, brief=False: None)
    monkeypatch.setitem(loop._cache, "on", True)


def _ctx(deadline=60.0):
    return tools.ToolContext(organization_ids=list(ORGS), subject="test", deadline=time.monotonic() + deadline)


# ── Tool scope and errors ────────────────────────────────────────────────────

def test_scope_narrows_but_never_widens():
    ctx = _ctx()
    assert ctx.scope(None) == ORGS
    assert ctx.scope([25]) == [25]
    with pytest.raises(ToolInputError, match="not selected"):
        ctx.scope([25, 99])


def test_query_metrics_runs_on_the_narrowed_scope_without_the_scope_field(monkeypatch):
    seen = {}

    def fake_run(params, *, organization_ids, subject, deadline):
        seen.update(params=params, orgs=organization_ids)
        return {"rows": [], "row_count": 0}

    monkeypatch.setattr(tools.query_metrics, "run", fake_run)
    result, ok = tools.execute("query_metrics", {"view": "pnl", "measures": ["pnl.revenue"], "organization_ids": [24]}, _ctx())
    assert ok and seen == {"params": {"view": "pnl", "measures": ["pnl.revenue"]}, "orgs": [24]}


def test_a_scope_outside_the_chat_is_an_error_for_the_model(monkeypatch):
    monkeypatch.setattr(tools.query_metrics, "run", lambda *a, **k: pytest.fail("must not run"))
    result, ok = tools.execute("query_metrics", {"view": "pnl", "organization_ids": [7]}, _ctx())
    assert not ok and "not selected" in result["error"]


@pytest.mark.parametrize("error,expected", [
    (ToolInputError("Unknown measure 'pnl.ebitda'"), "Unknown measure"),
    (CubeError("down"), tools.FIGURES_UNAVAILABLE),
    (RuntimeError("boom"), "could not be answered"),
])
def test_tool_failures_become_errors_never_exceptions(monkeypatch, error, expected):
    def boom(*a, **k):
        raise error
    monkeypatch.setattr(tools.list_documents, "run", boom)
    result, ok = tools.execute("list_documents", {"type": "bills"}, _ctx())
    assert not ok and expected in result["error"]


def test_unknown_tool():
    result, ok = tools.execute("run_sql", {"sql": "select 1"}, _ctx())
    assert not ok and "Unknown tool" in result["error"]


def test_specs_without_cube_keep_law_and_guide(monkeypatch):
    def no_cube(*a, **k):
        raise CubeError("down")
    monkeypatch.setattr(tools, "get_catalog", no_cube)
    names = [s["toolSpec"]["name"] for s in tools.specs(_ctx())]
    assert names == ["search_vat_kb", "app_guide"]


# ── The loop ─────────────────────────────────────────────────────────────────

class FakeAdapter:
    """Plays back scripted Bedrock responses, in order across adapters, and records what each was sent."""
    script = []
    step = 0
    log = []

    def __init__(self, model_id, label=""):
        self.model_id, self.sent, self.calls = model_id, [], 0

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        self.sent.append([dict(m) for m in messages])
        FakeAdapter.log.append({"model": self.model_id, "system": system, "tools": tools_})
        step = FakeAdapter.script[min(FakeAdapter.step, len(FakeAdapter.script) - 1)]
        FakeAdapter.step += 1
        self.calls += 1
        return step

    def get_token_usage(self):
        return {"llm_calls": self.calls, "cost_usd": 0.0}


def tool_use(name, params, uid="t1"):
    return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": uid, "name": name, "input": params}}]}}}


def final(text):
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


@pytest.fixture
def agent(monkeypatch):
    FakeAdapter.step, FakeAdapter.log = 0, []
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "query_metrics"}}])
    calls = []

    def fake_execute(name, params, ctx):
        calls.append((name, params))
        return {"rows": [{"organization_id": 24, "revenue": "5809352.00"}], "row_count": 1}, True

    monkeypatch.setattr(loop.tools, "execute", fake_execute)
    return calls


def test_one_tool_call_then_a_grounded_answer(agent):
    FakeAdapter.script = [tool_use("query_metrics", {"view": "pnl", "measures": ["pnl.revenue"]}),
                          final("Org One earned AED 5,809,352 in revenue this year.")]
    result = loop.run_agent("Revenue this year?", ORGS, META)
    assert result.status == "ok"
    assert result.answer.startswith("Org One earned")
    assert [c["name"] for c in result.tool_calls] == ["query_metrics"] and result.tool_calls[0]["rows"] == 1
    assert result.verification["grounded"] is True


def test_a_negative_figure_written_with_a_minus_is_grounded(monkeypatch, agent):
    monkeypatch.setattr(loop.tools, "execute", lambda name, params, ctx: (
        {"rows": [{"organization_id": 27, "revenue": "-832224.00"}], "row_count": 1}, True))
    FakeAdapter.script = [tool_use("query_metrics", {}), final("Org Four's revenue was AED -832,224.")]
    assert loop.run_agent("Revenue?", ORGS, META).verification["grounded"] is True


def test_a_cut_off_answer_is_asked_once_to_be_shorter(agent):
    cut = {"stopReason": "max_tokens", "output": {"message": {"role": "assistant", "content": []}}}
    FakeAdapter.script = [tool_use("query_metrics", {}), cut, final("Short summary: AED 5,809,352.")]
    result = loop.run_agent("Monthly trend since 2025", ORGS, META)
    assert result.answer == "Short summary: AED 5,809,352." and result.usage["llm_calls"] == 3


def test_an_invented_figure_is_reported(agent):
    FakeAdapter.script = [tool_use("query_metrics", {}), final("Revenue was AED 7,777,777.")]
    result = loop.run_agent("Revenue?", ORGS, META)
    assert result.verification["grounded"] is False


def test_the_tool_budget_is_enforced(agent):
    FakeAdapter.script = [tool_use("query_metrics", {}, uid=f"t{i}") for i in range(10)] + [final("done")]
    result = loop.run_agent("Loop forever", ORGS, META, max_tool_calls=2)
    assert len(agent) == 2
    assert any(c.get("error") == loop.BUDGET_USED for c in result.tool_calls)


def test_the_model_is_told_to_answer_when_the_budget_runs_out(agent):
    FakeAdapter.script = [tool_use("query_metrics", {}), final("done")]
    adapters = []
    original = loop.BedrockAdapter

    def capture(*args, **kwargs):
        adapters.append(original(*args, **kwargs))
        return adapters[-1]

    loop.BedrockAdapter = capture
    try:
        loop.run_agent("One call only", ORGS, META, max_tool_calls=1)
    finally:
        loop.BedrockAdapter = original
    last_user = adapters[0].sent[-1][-1]
    assert {"text": loop.WRAP_UP} in last_user["content"]


def test_out_of_time_says_so(agent):
    FakeAdapter.script = [tool_use("query_metrics", {})]
    result = loop.run_agent("Slow", ORGS, META, deadline_seconds=loop.MIN_SECONDS_FOR_MODEL_CALL - 1)
    assert result.status == "deadline" and result.answer == loop.OUT_OF_TIME


def test_model_errors_never_raise(monkeypatch):
    class Broken(FakeAdapter):
        def converse_with_tools(self, *a, **k):
            raise RuntimeError("bedrock down")
    FakeAdapter.step, FakeAdapter.log = 0, []
    monkeypatch.setattr(loop, "BedrockAdapter", Broken)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [])
    result = loop.run_agent("Anything", ORGS, META)
    assert result.status == "error" and result.answer == ""


def test_history_alternates_and_starts_with_the_user():
    history = [{"role": "assistant", "content": "hello"},
               {"role": "user", "content": "Compare revenue this year"},
               {"role": "assistant", "content": "Org One leads."},
               {"role": "user", "content": "and"}, {"role": "user", "content": "last quarter?"}]
    messages = loop._messages(history, "now only the top 2")
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert messages[2]["content"][0]["text"] == "and\n\nlast quarter?"
    assert messages[-1]["content"][0]["text"] == "now only the top 2"


# ── Law route, answer model, parallel calls, caching ─────────────────────────

def test_law_questions_take_the_knowledge_base_answer(monkeypatch, agent):
    monkeypatch.setattr(loop.law, "answer", lambda question, messages, brief=False: loop.law.LawAnswer(
        answer="Correct it in the next return [1].", usage={"llm_calls": 1, "cost_usd": 0.01}))
    FakeAdapter.script = [final("must not be called")]
    result = loop.run_agent("Voluntary disclosure or next return for AED 8,000?", ORGS, META)
    assert result.route == "law" and result.answer.startswith("Correct it") and FakeAdapter.log == []


def test_the_answer_model_writes_after_the_tools(agent):
    FakeAdapter.script = [tool_use("query_metrics", {}), final("Revenue AED 5,809,352.")]
    loop.run_agent("Revenue?", ORGS, META, model_id="planner", answer_model_id="writer")
    assert [c["model"] for c in FakeAdapter.log] == ["planner", "writer"]


def test_one_turns_tool_calls_all_run_and_the_budget_cuts_the_rest(agent):
    two = {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "a", "name": "query_metrics", "input": {"period": "2026"}}},
        {"toolUse": {"toolUseId": "b", "name": "query_metrics", "input": {"period": "2025"}}},
        {"toolUse": {"toolUseId": "c", "name": "query_metrics", "input": {"period": "2024"}}}]}}}
    FakeAdapter.script = [two, final("Growth: AED 5,809,352.")]
    result = loop.run_agent("Growth over three years", ORGS, META, max_tool_calls=2)
    assert len(agent) == 2
    assert [c["ok"] for c in result.tool_calls] == [True, True, False]
    assert result.tool_calls[2]["error"] == loop.BUDGET_USED


def test_system_prompt_and_tools_are_marked_for_caching(agent):
    FakeAdapter.script = [final("No figures needed.")]
    loop.run_agent("Hello", ORGS, META)
    assert FakeAdapter.log[0]["system"][-1] == loop._CACHE_POINT
    assert FakeAdapter.log[0]["tools"][-1] == loop._CACHE_POINT


def test_a_refused_cache_point_is_dropped_for_good(monkeypatch, agent):
    class NoCache(FakeAdapter):
        def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
            if loop._CACHE_POINT in system:
                raise RuntimeError("ValidationException: cachePoint is not supported for this model")
            return super().converse_with_tools(system, messages, tools_, max_tokens, purpose)
    monkeypatch.setattr(loop, "BedrockAdapter", NoCache)
    FakeAdapter.script = [final("Plain answer.")]
    result = loop.run_agent("Hello", ORGS, META)
    assert result.answer == "Plain answer." and loop._cache["on"] is False


@pytest.mark.parametrize("question", [
    "We found an error in last quarter's return that understated tax by AED 8,000. Voluntary disclosure, or correct it in the next return?",
    "A company leaves our VAT tax group mid-year. What output tax and input tax adjustments are needed?",
    "What is VAT?",
    "When does e-invoicing become mandatory for a business with AED 60 million revenue?",
])
def test_law_questions_are_not_mistaken_for_organization_questions(question):
    assert not loop.law.about_the_organizations(question)


@pytest.mark.parametrize("question", [
    "What is VAT for each organization?", "Compare VAT payable for Q2 2026", "Compare tax payable",
    "Input VAT vs output VAT per entity this year", "Which entities have VAT return due this month?",
])
def test_organization_questions_never_take_the_law_route(question):
    assert loop.law.about_the_organizations(question)
    assert _REAL_LAW_ANSWER(question, []) is None


def test_progress_is_told_about_each_tool_before_it_runs(agent):
    FakeAdapter.script = [tool_use("query_metrics", {"view": "pnl"}), final("AED 5,809,352.")]
    seen = []
    loop.run_agent("Revenue?", ORGS, META, progress=lambda name, params: seen.append((name, params, len(agent))))
    assert seen == [("query_metrics", {"view": "pnl"}, 0)]


def test_the_law_route_passes_its_sources_block_on(monkeypatch, agent):
    block = {"type": "fta_sources", "sources": [{"n": 1}]}
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: loop.law.LawAnswer(answer="Rule [1].", blocks=[block]))
    result = loop.run_agent("what is a deemed supply", [24], {})
    assert result.route == "law" and result.blocks == [block]


def test_app_guide_and_figure_results_are_kept_for_links_and_files(agent):
    FakeAdapter.script = [tool_use("app_guide", {"question": "how do I create an invoice"}, "g1"),
                          tool_use("query_metrics", {"view": "pnl", "measures": ["pnl.revenue"]}, "q1"),
                          final("Go to Sales > Create invoice.")]
    result = loop.run_agent("How do I create an invoice?", ORGS, META)
    assert [d["tool"] for d in result.data] == ["app_guide", "query_metrics"]



def test_law_answers_are_checked_against_the_cited_passages(monkeypatch, agent):
    block = {"type": "fta_sources", "sources": [{"n": 1}], "evidence_numbers": ["5", "375000"]}
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: loop.law.LawAnswer(
        answer="Register when supplies exceed AED 375,000 [1].", blocks=[block]))
    result = loop.run_agent("When must I register for VAT?", [24], {})
    assert result.verification.get("grounded") is True


def test_brief_mode_reaches_the_prompt_and_the_law_route(monkeypatch, agent):
    seen = {}
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: seen.update(brief=brief) or None)
    FakeAdapter.script = [final("AED 5,809,352.")]
    loop.run_agent("Revenue this year?", ORGS, META, brief=True)
    assert seen["brief"] is True and "Brief mode" in FakeAdapter.log[0]["system"][0]["text"]



def test_context_size_is_the_largest_single_call():
    from gemini_brain.reasoning.bedrock_client import BedrockAdapter
    adapter = BedrockAdapter("in.anthropic.claude-sonnet-5")
    adapter._track_usage({"inputTokens": 300, "cacheReadInputTokens": 10000, "outputTokens": 100})
    adapter._track_usage({"inputTokens": 2500, "cacheReadInputTokens": 10000, "outputTokens": 400})
    usage = adapter.get_token_usage()
    assert usage["context_tokens"] == 12900 and usage["input_tokens"] == 2800

    class Fixed:
        def __init__(self, usage):
            self.usage = usage

        def get_token_usage(self):
            return self.usage
    total = loop._usage(Fixed({"context_tokens": 9000, "input_tokens": 10}), Fixed({"context_tokens": 13000, "input_tokens": 5}))
    assert total == {"context_tokens": 13000, "input_tokens": 15}
