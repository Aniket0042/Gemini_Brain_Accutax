"""Dashboard widget insights served by the AI API (/api/v1/insight/*): auth, org scope, every sample widget."""
import json
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.insights import auth, cache, rate_limit, routes
from gemini_brain.insights.auth import AuthContext

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "insight_samples"
NARRATIVE = {"summary": "Revenue grew.", "insights": [{"type": "tip", "text": "Chase overdue invoices."}]}


class _Bedrock:
    def __init__(self):
        self.calls = []

    async def generate_narrative(self, name, facts):
        self.calls.append((name, facts))
        return NARRATIVE


@pytest.fixture
def client(monkeypatch):
    cache._cache.clear()
    rate_limit._hits.clear()
    auth._introspection_cache.clear()
    bedrock = _Bedrock()
    monkeypatch.setattr(routes, "get_bedrock_client", lambda: bedrock)
    c = TestClient(app)
    c.bedrock = bedrock
    return c


def _signed_in(org_ids):
    async def ctx(request=None, credentials=None):
        return AuthContext(user_id=7, organization_ids=org_ids)
    return ctx


def _sample(name):
    return json.loads((SAMPLES / name).read_text())


def test_widget_catalogue_lists_all_nine():
    r = TestClient(app).get("/api/v1/insight/widgets")
    assert r.status_code == 200
    assert len(r.json()) == 9 and "profit-loss" in r.json()


@pytest.mark.parametrize("name", sorted(p.name for p in SAMPLES.glob("*.json")))
def test_every_sample_widget_gets_an_insight(client, name):
    body = _sample(name)
    app.dependency_overrides[auth.get_insight_auth] = _signed_in([body["organizationId"]])
    try:
        r = client.post("/api/v1/insight/generate", json=body)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["widgetId"] == body["widgetId"] and out["summary"] == "Revenue grew." and not out["cached"]
    assert any(b["text"] == "Chase overdue invoices." for b in out["bullets"])
    # Only precomputed facts go to the model, never the raw request.
    assert len(client.bedrock.calls) == 1 and "organizationId" not in client.bedrock.calls[0][1]


def test_second_identical_request_is_served_from_cache(client):
    body = _sample("total-revenue.json")
    app.dependency_overrides[auth.get_insight_auth] = _signed_in([body["organizationId"]])
    try:
        client.post("/api/v1/insight/generate", json=body)
        r = client.post("/api/v1/insight/generate", json=body)
    finally:
        app.dependency_overrides.clear()
    assert r.json()["cached"] is True and len(client.bedrock.calls) == 1


def test_another_companys_widget_is_refused(client):
    body = _sample("net-profit.json")
    app.dependency_overrides[auth.get_insight_auth] = _signed_in([body["organizationId"] + 1])
    try:
        r = client.post("/api/v1/insight/generate", json=body)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 403 and client.bedrock.calls == []


def test_no_token_is_refused(client):
    r = client.post("/api/v1/insight/generate", json=_sample("net-profit.json"))
    assert r.status_code == 401 and client.bedrock.calls == []


def test_token_checked_against_accutax_and_scoped_to_its_orgs(client):
    body = _sample("cash-balance.json")
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [{"id": body["organizationId"]}]}

    class _Http:
        async def get(self, path, params=None, headers=None):
            seen.update(path=path, params=params, auth=headers["Authorization"])
            return _Resp()

    token = jwt.encode({"userId": 15}, "accutax-signs-this-not-us", algorithm="HS256")
    with patch.object(auth, "_get_http_client", lambda: _Http()):
        r = client.post("/api/v1/insight/generate", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert seen == {"path": "/auth/all_organizations", "params": {"user_id": 15}, "auth": f"Bearer {token}"}


def test_gateway_mode_uses_the_gateway_user_not_the_bearer(client, monkeypatch):
    from gemini_brain.api import auth as api_auth
    from gemini_brain.config.settings import settings

    body = _sample("total-expenses.json")
    monkeypatch.setattr(settings, "ai_auth_mode", "gateway")
    monkeypatch.setattr(api_auth, "get_current_user",
                        lambda token, request: api_auth.CurrentUser(9, "g@example.test", [body["organizationId"]]))
    with patch.object(auth, "get_auth_context", side_effect=AssertionError("bearer path must not run")):
        r = client.post("/api/v1/insight/generate", json=body)
    assert r.status_code == 200, r.text
