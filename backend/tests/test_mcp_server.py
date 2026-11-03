"""
test_mcp_server.py — the MCP connector: bearer check, tenant isolation through the tools, and answer wording.

The tools call the real FastAPI app in-process (httpx ASGITransport), so org scoping is the API's own
authorize_org_scope, not a copy. Only the agent and the live org lookups are faked.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.mcpserver.exceptions import ToolError
from starlette.testclient import TestClient

from gemini_brain.api.app import create_app
from gemini_brain.api.auth import create_access_token
from gemini_brain.mcp_server.server import build_server, render_answer, render_table
from gemini_brain.mcp_server.upstream import Upstream
from gemini_brain.mcp_server.verifier import AccutaxTokenVerifier


@pytest.fixture(scope="module")
def api_app():
    with patch("gemini_brain.api.auth.init_auth_db"):
        yield create_app()


@pytest.fixture(autouse=True)
def _org_access_from_token(monkeypatch):
    """No live Accutax or database org lookup: access comes from the org list signed into the token."""
    from gemini_brain.api import auth, routes

    monkeypatch.setattr(auth, "_fetch_accutax_orgs", lambda token, user_id: None)
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="", **_: None)
    monkeypatch.setattr(routes, "_resolve_tenants_for_org_ids",
                        lambda ids: [{"id": i, "name": f"Org {i}", "display_name": f"Org {i}"} for i in ids])
    auth._DB_ORG_CACHE.clear()
    auth._ACCUTAX_ORG_CACHE.clear()


@pytest.fixture
def agent_calls():
    """Fake the agent. Records the orgs each question ran on."""
    calls = []

    def answer(question, orgs, org_meta, **_):
        calls.append({"question": question, "orgs": list(orgs)})
        return {
            "answer": f"Revenue for org {orgs[0]} is AED 1,000.",
            "blocks": [
                {"type": "markdown", "content": "duplicate of answer"},
                {"type": "table", "columns": [{"key": "month", "label": "Month"}, {"key": "aed", "label": "AED"}],
                 "rows": [{"month": "Jan", "aed": "600"}, {"month": "Feb", "aed": "400"}], "total_rows": 2},
            ],
            "status": "ok",
            "request_id": "req-1",
            "sql_traces": [{"sql": "select secret"}],
            "token_usage": {"input_tokens": 1, "output_tokens": 1, "llm_calls": 1, "cost_usd": 0.5,
                            "elapsed_seconds": 0.1},
            "routing_info": {"type": 1, "type_label": "Figures", "path": "agent_tools", "reason": "Test"},
        }

    with patch("gemini_brain.api.routes.agent_preview.answer", answer):
        yield calls


def _token(user_id: int = 101, orgs: list[int] | None = None) -> str:
    return create_access_token(user_id, f"user{user_id}@example.com", orgs if orgs is not None else [24])


def _call(api_app, token: str, tool: str, args: dict):
    """Call a tool as the MCP transport would after the bearer passed: the access token in the auth context."""
    async def run():
        upstream = Upstream("http://api", transport=httpx.ASGITransport(app=api_app))
        server = build_server(upstream)
        access = await AccutaxTokenVerifier().verify_token(token)
        assert access is not None
        ctx = auth_context_var.set(AuthenticatedUser(access))
        try:
            result = await server.call_tool(tool, args)
        finally:
            auth_context_var.reset(ctx)
            await upstream.aclose()
        return result.content[0].text

    return asyncio.run(run())


class TestVerifier:

    def test_accepts_own_token(self):
        access = asyncio.run(AccutaxTokenVerifier().verify_token(_token(7)))
        assert access is not None and access.subject == "7" and "accutax.read" in access.scopes

    def test_refuses_garbage(self):
        assert asyncio.run(AccutaxTokenVerifier().verify_token("not-a-jwt")) is None

    def test_refuses_token_signed_with_another_secret(self):
        import jwt
        forged = jwt.encode({"sub": "7", "allowed_org_ids": [24]}, "someone-elses-secret", algorithm="HS256")
        assert asyncio.run(AccutaxTokenVerifier().verify_token(forged)) is None


class TestToolsThroughApi:

    def test_ask_runs_on_own_org(self, api_app, agent_calls):
        text = _call(api_app, _token(orgs=[24, 25]), "ask_accutax",
                     {"question": "What was revenue?", "organization_id": 25})
        assert agent_calls == [{"question": "What was revenue?", "orgs": [25]}]
        assert "AED 1,000" in text and "| Jan | 600 |" in text

    def test_ask_without_org_uses_first_allowed(self, api_app, agent_calls):
        _call(api_app, _token(orgs=[24, 25]), "ask_accutax", {"question": "What was revenue?"})
        assert agent_calls[0]["orgs"] == [24]

    def test_foreign_org_refused_before_agent_runs(self, api_app, agent_calls):
        with pytest.raises(ToolError, match="not in the caller's allowed tenant list"):
            _call(api_app, _token(orgs=[24]), "ask_accutax", {"question": "What was revenue?", "organization_id": 30})
        assert agent_calls == []

    def test_answer_carries_no_traces_or_cost(self, api_app, agent_calls):
        text = _call(api_app, _token(), "ask_accutax", {"question": "What was revenue?"})
        assert "select secret" not in text and "cost" not in text.lower()
        assert "duplicate of answer" not in text

    def test_list_organizations_only_own(self, api_app):
        text = _call(api_app, _token(orgs=[24, 26]), "list_organizations", {})
        assert "| 24 |" in text and "| 26 |" in text and "| 25 |" not in text

    def test_unknown_agent_refused_by_schema(self, api_app, agent_calls):
        with pytest.raises(ToolError):
            _call(api_app, _token(), "ask_accutax", {"question": "What was revenue?", "agent": "payroll"})
        assert agent_calls == []


class TestHttpEndpoint:

    @pytest.fixture
    def mcp_client(self):
        app = build_server(Upstream("http://unused")).streamable_http_app(stateless_http=True)
        with TestClient(app, base_url="http://localhost:8012") as client:
            yield client

    def test_no_bearer_is_401_with_resource_metadata(self, mcp_client):
        resp = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream"})
        assert resp.status_code == 401
        assert "resource_metadata" in resp.headers.get("www-authenticate", "")

    def test_bad_bearer_is_401(self, mcp_client):
        resp = mcp_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream",
                                        "Authorization": "Bearer nope"})
        assert resp.status_code == 401

    def test_protected_resource_metadata_served(self, mcp_client):
        resp = mcp_client.get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource"].endswith("/mcp") and body["authorization_servers"]


class TestRendering:

    def test_table_caps_rows_and_says_so(self):
        block = {"type": "table", "columns": ["a"], "rows": [{"a": i} for i in range(5)], "total_rows": 80}
        text = render_table(block, max_rows=3)
        assert "| 2 |" in text and "| 3 |" not in text and "3 of 80 rows shown" in text

    def test_pipes_in_cells_escaped(self):
        text = render_table({"columns": ["a"], "rows": [{"a": "x|y"}]}, max_rows=5)
        assert "x\\|y" in text

    def test_fta_sources_and_degraded_notice_kept(self):
        text = render_answer({
            "answer": "Zero-rated.", "status": "degraded", "notice": {"message": "Some data was unavailable."},
            "blocks": [{"type": "fta_sources", "text": "**Sources — Federal Tax Authority**\n\n1. [VATP001](u)"}],
            "agent": {"name": "VAT agent"}, "request_id": "r9",
        })
        assert "VATP001" in text and "Some data was unavailable." in text and "VAT agent" in text
