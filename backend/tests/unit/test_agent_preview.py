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
    def answer(question, orgs, org_meta, *, session_id, user_id, db_name="", progress=None, brief=False):
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

    def fake_run_agent(question, orgs, meta, *, history, subject, progress, **_):
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

    def fake_run_agent(question, orgs, meta, *, history, subject, progress, **_):
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


def test_export_rows_take_the_largest_figure_result_with_plain_column_names():
    data = [
        {"tool": "query_metrics", "result": {"rows": [{"pnl.organization_id": 24, "pnl.organization_name": "A",
                                                       "pnl.revenue": "10"}]}},
        {"tool": "query_metrics", "result": {"rows": [
            {"pnl.organization_id": 24, "pnl.organization_name": "A", "pnl.transaction_date.month": "2026-01-01",
             "pnl.revenue": "4"},
            {"pnl.organization_id": 24, "pnl.organization_name": "A", "pnl.transaction_date.month": "2026-02-01",
             "pnl.revenue": "6"}]}},
    ]
    assert preview.export_rows(data) == [{"organization_name": "A", "month": "2026-01-01", "revenue": "4"},
                                         {"organization_name": "A", "month": "2026-02-01", "revenue": "6"}]


def test_export_rows_flatten_the_cash_forecast_weeks():
    data = [{"tool": "cash_forecast", "result": {"organizations": [
        {"organization_name": "A", "currency": "AED", "weeks": [{"week_start": "2026-10-08", "closing_cash": "5"}]}]}}]
    assert preview.export_rows(data) == [{"organization": "A", "currency": "AED", "week_start": "2026-10-08",
                                          "closing_cash": "5"}]


def test_a_file_request_gets_download_blocks_from_the_agent_figures(monkeypatch):
    import gemini_brain.artifacts.attach as attach
    seen = {}

    def fake_attach(query, data, blocks, **kw):
        seen.update(query=query, data=data, **kw)
        return [{"type": "artifact", "kind": "pdf", "id": "a1"}]

    monkeypatch.setattr(attach, "attach_delivery", fake_attach)
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(
        answer="Revenue AED 10.", status="ok",
        data=[{"tool": "query_metrics", "result": {"rows": [{"pnl.organization_name": "A", "pnl.revenue": "10"}]}}]))
    out = preview.answer("Export my P&L for this year as a PDF", [24], lambda: {24: {"name": "A"}},
                         session_id=None, user_id=501)
    assert out["blocks"] == [{"type": "artifact", "kind": "pdf", "id": "a1"}]
    assert seen["data"] == [{"organization_name": "A", "revenue": "10"}]
    assert seen["organization_id"] == 24 and seen["org_name"] == "A" and seen["answer_text"] == "Revenue AED 10."


def test_a_plain_question_gets_no_file(monkeypatch):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="AED 10.", status="ok"))
    assert preview.answer("Revenue this year?", [24], lambda: {}, session_id=None, user_id=501)["blocks"] is None


def test_cash_forecast_step_label():
    assert preview.step_label("cash_forecast", {"weeks": 8}) == "Projecting cash…"
    assert preview.step_label("list_documents", {"type": "journal_lines"}) == "Listing documents: journal lines…"


@pytest.fixture
def _primary(monkeypatch):
    monkeypatch.setattr(preview.settings, "agent_mode", "primary")


def test_primary_mode_answers_everyone_with_the_agent(_primary, monkeypatch):
    calls = []
    monkeypatch.setattr(routes.agent_preview, "answer", _fake_answer(calls))
    response = asyncio.run(routes.run_query(QueryRequest(query="Revenue?", model="auto", organization_id=24),
                                            current_user=OTHER))
    assert response.answer.startswith("Org One revenue") and calls[0]["orgs"] == [24]
    assert routes._preview_scope(QueryRequest(query="Revenue?", model="claude-sonnet"), OTHER, []) == [24]


def test_primary_mode_serves_one_model_and_hides_the_picker(_primary):
    catalog = routes.list_model_catalog(current_user=OTHER)
    assert [m.key for m in catalog.models] == [preview.MODEL_KEY]
    assert catalog.models[0].label == "Accutax AI" and "Preview" not in catalog.models[0].description
    assert catalog.picker_hidden and catalog.default_model == preview.MODEL_KEY


def test_primary_answers_are_labelled_accutax_ai(_primary, monkeypatch):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="AED 10.", status="ok"))
    out = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=501)
    assert out["policy"]["model_label"] == "Accutax AI"


def test_other_modes_keep_the_picker():
    catalog = routes.list_model_catalog(current_user=OTHER)
    assert not catalog.picker_hidden and preview.MODEL_KEY not in [m.key for m in catalog.models]


def test_law_answers_keep_their_fta_sources_for_the_citation_chips(monkeypatch):
    sources = {"type": "fta_sources", "sources": [{"n": 1, "title": "VAT Public Clarification", "url": "https://x"}]}
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(
        answer="Use the open market value [1].", status="ok", route="law", blocks=[sources]))
    out = preview.answer("how do I value the deemed supply of services", [24], lambda: {}, session_id=None,
                         user_id=501)
    assert out["blocks"] == [sources] and out["routing_info"]["path"] == "agent_law"



def test_brief_reaches_the_agent(monkeypatch):
    seen = {}
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: seen.update(k) or loop.AgentResult(answer="AED 10.", status="ok"))
    preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=1, brief=True)
    assert seen["brief"] is True


def test_the_stream_route_passes_brief(monkeypatch):
    seen = {}

    def fake(question, orgs, org_meta, **kw):
        seen.update(kw)
        return {"answer": "x", "status": "ok"}
    monkeypatch.setattr(routes.agent_preview, "answer", fake)
    monkeypatch.setattr(routes, "_agent_org_meta", lambda orgs, user: lambda: {})
    payload = QueryRequest(query="Revenue?", model=preview.MODEL_KEY, organization_id=24, brief=True)
    list(routes._preview_events(payload, TESTER, [24]))
    assert seen["brief"] is True


def test_multi_org_answers_carry_org_chips(monkeypatch):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="AED 10.", status="ok"))
    out = preview.answer("Revenue?", [24, 25], lambda: {24: {"name": "A", "currency": "AED"}}, session_id=None, user_id=1)
    assert out["organizations"] == [{"id": 24, "name": "A", "currency": "AED", "status": "ok"},
                                    {"id": 25, "name": "Organization 25", "currency": "", "status": "ok"}]
    single = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=1)
    assert single["organizations"] == []


@pytest.mark.parametrize("status, calls, expected_status, code", [
    ("deadline", [], "degraded", "UPSTREAM_TIMEOUT"),
    ("error", [], "failed", "MODEL_UNAVAILABLE"),
    ("ok", [{"name": "query_metrics", "ok": False,
             "error": "Figures are temporarily unavailable. Tell the user so; never estimate figures."}],
     "partial", "UPSTREAM_UNAVAILABLE"),
    ("ok", [], "ok", None),
])
def test_notice_cards_match_the_outcome(monkeypatch, status, calls, expected_status, code):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="x", status=status, tool_calls=calls))
    out = preview.answer("Revenue?", [24, 25], lambda: {}, session_id=None, user_id=1)
    assert out["status"] == expected_status
    assert (out["notice"] or {}).get("code") == code
    if code == "UPSTREAM_UNAVAILABLE":
        assert all(o["status"] == "failed" for o in out["organizations"])


def test_the_context_window_meter_shows_the_latest_context_size(monkeypatch):
    import gemini_brain.memory.context_window as cw
    import gemini_brain.memory.session_memory as memory
    seen = []
    monkeypatch.setattr(cw, "set_context_window_usage",
                        lambda sid, tokens, db_name="": seen.append((sid, tokens)) or {"used": tokens})
    for name in ("get_history_by_session", "ensure_session", "save_message_by_session"):
        monkeypatch.setattr(memory, name, lambda *a, **k: [] if name == "get_history_by_session" else True)
    sid = "5f0c8a43-1111-4222-8333-944455556666"
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="x", status="ok", usage={
        "input_tokens": 1200, "cache_read_tokens": 20000, "output_tokens": 600, "llm_calls": 2,
        "context_tokens": 11500}))
    assert preview.answer("Revenue?", [24], lambda: {}, session_id=sid, user_id=1)["context_window"] == {"used": 11500}
    # Without the per-call figure: the tokens read and written, over the model calls.
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="x", status="ok", usage={
        "input_tokens": 1200, "cache_read_tokens": 20000, "output_tokens": 600, "llm_calls": 2}))
    preview.answer("Revenue?", [24], lambda: {}, session_id=sid, user_id=1)
    assert seen == [(sid, 11500), (sid, 10900)]


def test_a_reopened_thread_gets_its_meter(monkeypatch):
    import gemini_brain.memory.context_window as cw
    import gemini_brain.memory.session_memory as memory
    sid = "5f0c8a43-1111-4222-8333-944455556666"
    monkeypatch.setattr(memory, "get_session_record", lambda s: {"id": s, "organization_id": 24})
    monkeypatch.setattr(memory, "verify_session_ownership", lambda s, u: True)
    monkeypatch.setattr(memory, "session_scope", lambda rec: [24])
    monkeypatch.setattr(memory, "get_transcript_by_session", lambda s, limit=50: [])
    monkeypatch.setattr(routes, "authorize_org_scope", lambda *a, **k: None)
    monkeypatch.setattr(cw, "get_context_window_usage", lambda s, db_name="": {"used": 11000, "limit": 200000})
    out = routes.get_chat_session_messages(sid, current_user=TESTER)
    assert out.context_window == {"used": 11000, "limit": 200000}
