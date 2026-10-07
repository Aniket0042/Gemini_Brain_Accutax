"""Agent preview: who sees it, how a request is routed, what it streams and saves. Agent and Cube faked."""
import asyncio
import json

import pytest

from gemini_brain.agent import loop, preview
from gemini_brain.api import routes
from gemini_brain.api.auth import CurrentUser
from gemini_brain.api.models import QueryRequest

TESTER = CurrentUser(user_id=501, email="TestUserDummy2@test.com", allowed_org_ids=[24, 25, 27])
OTHER = CurrentUser(user_id=777, email="someone@test.com", allowed_org_ids=[24])


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch):
    monkeypatch.setattr(preview.settings, "agent_preview_users", "testuserdummy2@test.com, 9001")


def test_allowlist_matches_email_in_any_case_or_user_id(monkeypatch):
    assert preview.allowed(TESTER)
    assert not preview.allowed(OTHER)
    assert preview.allowed(CurrentUser(user_id=9001, email="", allowed_org_ids=[]))
    monkeypatch.setattr(preview.settings, "agent_preview_users", "")
    assert not preview.allowed(TESTER)


def test_picker_shows_the_preview_only_to_allowed_users():
    keys = lambda user: [m.key for m in routes.list_model_catalog(current_user=user).models]
    assert keys(TESTER)[0] == preview.MODEL_KEY
    assert preview.MODEL_KEY not in keys(OTHER)


def test_preview_scope():
    ask = lambda model: QueryRequest(query="Revenue?", model=model)
    assert routes._preview_scope(ask(preview.MODEL_KEY), TESTER, [24, 25]) == [24, 25]
    assert routes._preview_scope(ask(preview.MODEL_KEY), TESTER, []) == [24]   # no org named: the first allowed
    assert routes._preview_scope(ask("auto"), TESTER, [24]) is None
    assert routes._preview_scope(ask(preview.MODEL_KEY), OTHER, [24]) is None  # stale selection: normal path


def _fake_answer(calls):
    def answer(question, orgs, org_meta, *, session_id, user_id, db_name="", progress=None):
        calls.append({"question": question, "orgs": orgs, "session_id": session_id, "user_id": user_id})
        if progress:
            progress("Fetching figures: pnl…")
            progress("Listing documents: bills…")
        return {"answer": "Org One revenue was AED 5,809,352.", "status": "ok",
                "token_usage": {"llm_calls": 2, "cost_usd": 0.02},
                "routing_info": {"type": 1, "type_label": "Figures (governed metrics)", "path": "agent_tools"},
                "policy": {"model": preview.MODEL_KEY, "model_label": preview.MODEL_LABEL, "auto": False}}
    return answer


def test_query_route_answers_with_the_agent(monkeypatch):
    calls = []
    monkeypatch.setattr(routes.agent_preview, "answer", _fake_answer(calls))
    monkeypatch.setattr(routes, "_org_meta", lambda user, orgs: {})
    payload = QueryRequest(query="Revenue this year?", model=preview.MODEL_KEY, organization_id=24)
    response = asyncio.run(routes.run_query(payload, current_user=TESTER))
    assert response.answer.startswith("Org One revenue")
    assert response.routing_info.path == "agent_tools"
    assert calls == [{"question": "Revenue this year?", "orgs": [24], "session_id": None, "user_id": 501}]


def test_stream_route_sends_a_status_per_tool_then_the_answer(monkeypatch):
    monkeypatch.setattr(routes.agent_preview, "answer", _fake_answer([]))
    monkeypatch.setattr(routes, "_org_meta", lambda user, orgs: {})
    payload = QueryRequest(query="Revenue?", model=preview.MODEL_KEY, organization_id=24)
    events = [json.loads(e[len("data: "):]) for e in routes._preview_events(payload, TESTER, [24])]
    assert [e.get("status") for e in events[:-1]] == ["Reading your question…", "Fetching figures: pnl…",
                                                      "Listing documents: bills…"]
    assert events[-1]["final_result"]["answer"].startswith("Org One revenue")


def test_answer_saves_the_turn_and_reports_the_route(monkeypatch):
    saved, scopes = [], []
    import gemini_brain.memory.session_memory as memory
    monkeypatch.setattr(memory, "get_history_by_session", lambda sid, limit=6, db_name="": [{"role": "user", "content": "earlier"}])
    monkeypatch.setattr(memory, "ensure_session", lambda sid, uid, db_name="", **scope: scopes.append(scope) or True)
    monkeypatch.setattr(memory, "save_message_by_session", lambda sid, role, text, db_name="": saved.append((role, text)))
    seen = {}

    def fake_run_agent(question, orgs, meta, *, history, subject, progress):
        seen.update(history=history, subject=subject)
        return loop.AgentResult(answer="Correct it in the next return [1].", status="ok", route="law", usage={"llm_calls": 1})

    monkeypatch.setattr(loop, "run_agent", fake_run_agent)
    out = preview.answer("AED 8,000 error: disclosure?", [24, 25], lambda: {}, session_id="5f0c8a43-1111-4222-8333-944455556666",
                         user_id=501)
    assert out["routing_info"]["path"] == "agent_law" and out["status"] == "ok"
    assert out["policy"]["model_label"] == preview.MODEL_LABEL
    assert seen == {"history": [{"role": "user", "content": "earlier"}], "subject": "agent-preview:501"}
    assert scopes == [{"organization_ids": [24, 25]}]
    assert saved == [("user", "AED 8,000 error: disclosure?"), ("assistant", "Correct it in the next return [1].")]


def test_step_labels():
    assert preview.step_label("query_metrics", {"view": "balance_sheet"}) == "Fetching figures: balance sheet…"
    assert preview.step_label("list_documents", {"type": "open_receivables"}) == "Listing documents: open receivables…"
    assert preview.step_label("search_vat_kb", {"question": "x"}) == "Searching UAE VAT law…"


def test_answer_returns_cube_and_model_traces_for_the_trace_cards(monkeypatch):
    from gemini_brain.observability.api_tracer import record_api_trace
    from gemini_brain.observability.llm_tracer import record_llm_trace

    def fake_run_agent(question, orgs, meta, *, history, subject, progress):
        record_api_trace(endpoint="cube:pnl", method="POST", status_code=200, outcome="ok", row_count=10, source="cube")
        record_llm_trace(model_id="in.anthropic.claude-sonnet-5", purpose="agent", input_tokens=100, output_tokens=20,
                         duration_ms=900.0)
        return loop.AgentResult(answer="AED 5,809,352.", status="ok")

    monkeypatch.setattr(loop, "run_agent", fake_run_agent)
    monkeypatch.setattr(preview.settings, "show_api_traces", True)
    monkeypatch.setattr(preview.settings, "show_llm_traces", True)
    out = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=501)
    assert [t["endpoint"] for t in out["api_traces"]] == ["cube:pnl"]
    assert out["llm_traces"][0]["purpose"] == "agent"
