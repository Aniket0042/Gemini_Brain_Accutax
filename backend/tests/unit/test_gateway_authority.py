"""The private AI service accepts only a live BFF delegation in gateway mode."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, create_access_token, decode_access_token
from gemini_brain.api.gateway_authority import load_gateway_configuration
from gemini_brain.api.models import QueryRequest
from gemini_brain.agent.tools import ToolContext, execute
from gemini_brain.config.settings import settings

TO_AI = "a" * 43
TO_BFF = "b" * 43
DELEGATION = "c" * 43


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({
        "upstream": "http://127.0.0.1:8000",
        "resolveUrl": "http://127.0.0.1:8081/internal/ai/resolve",
        "gatewayToAiKey": TO_AI,
        "aiToGatewayKey": TO_BFF,
    }))
    config.chmod(0o600)
    monkeypatch.setattr(settings, "ai_auth_mode", "gateway")
    monkeypatch.setattr(settings, "ai_gateway_config_file", str(config))
    load_gateway_configuration.cache_clear()
    yield config
    load_gateway_configuration.cache_clear()


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data or {"user_id": 5, "email": "u@example.test", "allowed_org_ids": [10]}

    def json(self):
        return self.data


def test_private_configuration_requires_two_keys_and_private_file(gateway: Path):
    assert load_gateway_configuration(str(gateway))["gatewayToAiKey"] == TO_AI
    gateway.chmod(0o644)
    load_gateway_configuration.cache_clear()
    with pytest.raises(RuntimeError):
        load_gateway_configuration(str(gateway))


def test_browser_jwt_cookie_and_password_routes_cannot_enter_gateway(gateway):
    client = TestClient(app)
    assert client.get("/api/v1/models", headers={"Authorization": "Bearer old-jwt"}).status_code == 401
    assert client.get("/api/v1/models", headers={
        "Authorization": f"Bearer {TO_AI}", "X-Accutax-Delegation": DELEGATION,
        "Cookie": "accutax_session_dev=forged",
    }).status_code == 401
    assert client.post("/api/v1/auth/login-json", json={"email": "u", "password": "p"}).status_code == 401
    with pytest.raises(HTTPException) as old_token:
        decode_access_token("old-jwt")
    assert old_token.value.status_code == 401
    with pytest.raises(RuntimeError):
        create_access_token(5, "u@example.test", [10])


def test_private_ai_mode_does_not_enable_browser_cors(gateway):
    from gemini_brain.api.app import create_app
    client = TestClient(create_app())
    result = client.options("/api/v1/tenants", headers={
        "Origin": "http://localhost:5175", "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in result.headers


def test_service_delegation_resolves_exact_method_path_and_current_orgs(gateway):
    client = TestClient(app)
    observed = []

    def resolve(url, **kwargs):
        observed.append((url, kwargs))
        return Response()

    with patch("httpx.post", resolve), \
         patch("gemini_brain.api.routes.fetch_accutax_accessible_orgs", return_value=[]), \
         patch("gemini_brain.api.routes._resolve_tenants_for_org_ids", return_value=[{"id": 10, "name": "Company A", "display_name": "Company A"}]):
        result = client.get("/api/v1/tenants", headers={
            "Authorization": f"Bearer {TO_AI}", "X-Accutax-Delegation": DELEGATION,
        })
    assert result.status_code == 200
    assert result.json()["user_id"] == 5
    assert [org["id"] for org in result.json()["tenants"]] == [10]
    assert observed[0][0] == "http://127.0.0.1:8081/internal/ai/resolve"
    assert observed[0][1]["headers"] == {"Authorization": f"Bearer {TO_BFF}"}
    assert observed[0][1]["json"] == {
        "delegation": DELEGATION, "method": "GET", "path": "/api/v1/tenants",
    }


def test_resolver_failure_or_wrong_identity_fails_closed(gateway):
    from gemini_brain.api.gateway_authority import GatewayAuthority
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "scheme": "http", "server": ("127.0.0.1", 8000),
                       "path": "/api/v1/tenants", "query_string": b"", "headers": [
                           (b"authorization", f"Bearer {TO_AI}".encode()),
                           (b"x-accutax-delegation", DELEGATION.encode()),
                       ]})
    authority = GatewayAuthority(request)
    with patch("httpx.post", side_effect=OSError("down")):
        with pytest.raises(HTTPException) as outage:
            authority.resolve()
    assert outage.value.status_code == 503
    with patch("httpx.post", side_effect=[Response(), Response(data={"user_id": 6, "allowed_org_ids": [10]})]):
        assert authority.resolve()["user_id"] == 5
        with pytest.raises(HTTPException) as switched:
            authority.resolve()
    assert switched.value.status_code == 503


def test_company_revocation_stops_a_tool_before_it_runs(gateway):
    class Revoked:
        def resolve(self):
            return {"allowed_org_ids": []}

    user = CurrentUser(5, "u@example.test", [10])
    user.gateway_authority = Revoked()
    with pytest.raises(HTTPException) as denied:
        user.ensure_current_scope([10])
    assert denied.value.status_code == 403
    context = ToolContext(organization_ids=[10], subject="proof", deadline=10**10,
                          authorize=lambda: user.ensure_current_scope([10]))
    with pytest.raises(HTTPException) as blocked:
        execute("app_guide", {"question": "test"}, context)
    assert blocked.value.status_code == 403


def test_gateway_query_rejects_database_selection(gateway):
    with pytest.raises(ValueError):
        QueryRequest(query="Revenue?", organization_id=10, db_name="another_database")
