"""Continue from the Accutax dashboard with a one-time code (POST /api/v1/auth/handoff)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from gemini_brain.api import auth, routes
from gemini_brain.api.app import app
from gemini_brain.api.auth import HandoffRedeemError, get_jwt_secret, redeem_accutax_handoff
from gemini_brain.config.settings import settings

CODE = "A" * 43
UPSTREAM = {
    "access_token": "nest.jwt.token", "user_id": 11, "email": "u@example.com", "name": "U Ser",
    "organization_id": 2, "current_page": "/invoices", "session_id": "77199b54-7d5d-454d-b75a-42ff08f2492a",
    "conversation": [
        {"role": "user", "content": "Receivables?", "timestamp": 1},
        {"role": "assistant", "content": "AED 10", "tableMarkdown": "| a |"},
        {"role": "system", "content": "dropped"},
        {"role": "user", "content": 5},
    ],
}


def _resp(status: int, body=None) -> MagicMock:
    r = MagicMock(status_code=status)
    r.json.return_value = body if body is not None else {}
    return r


@pytest.fixture
def live_orgs(monkeypatch):
    monkeypatch.setattr(auth, "_live_allowed_org_ids", lambda user_id, token="": [1, 2])
    monkeypatch.setattr(settings, "accutax_base_url", "http://accutax.test:8081")
    monkeypatch.setattr(settings, "handoff_service_key", "")


def test_redeem_posts_code_and_audience_without_key(live_orgs):
    with patch("httpx.post", return_value=_resp(200, UPSTREAM)) as post:
        out = redeem_accutax_handoff(CODE)
    url, kwargs = post.call_args.args[0], post.call_args.kwargs
    assert url == "http://accutax.test:8081/handoff/exchange"
    assert kwargs["json"] == {"code": CODE, "audience": "ai-chat"}
    assert kwargs["headers"] == {}
    assert out["user_id"] == 11 and out["allowed_org_ids"] == [1, 2]
    assert out["organization_id"] == 2 and out["current_page"] == "/invoices"


def test_redeem_sends_service_key_when_set(live_orgs, monkeypatch):
    monkeypatch.setattr(settings, "handoff_service_key", "k3y")
    with patch("httpx.post", return_value=_resp(200, UPSTREAM)) as post:
        redeem_accutax_handoff(CODE)
    assert post.call_args.kwargs["headers"] == {"x-handoff-service-key": "k3y"}


def test_redeem_keeps_only_user_and_assistant_text_turns(live_orgs):
    with patch("httpx.post", return_value=_resp(200, UPSTREAM)):
        out = redeem_accutax_handoff(CODE)
    assert out["conversation"] == [
        {"role": "user", "content": "Receivables?", "timestamp": 1},
        {"role": "assistant", "content": "AED 10", "tableMarkdown": "| a |"},
    ]


def test_dashboard_org_dropped_when_user_lost_access(live_orgs):
    with patch("httpx.post", return_value=_resp(200, dict(UPSTREAM, organization_id=9))):
        assert redeem_accutax_handoff(CODE)["organization_id"] is None


@pytest.mark.parametrize("upstream_status, ours", [(410, 410), (400, 410), (404, 410), (403, 502), (500, 502)])
def test_upstream_refusals_map_to_plain_statuses(live_orgs, upstream_status, ours):
    with patch("httpx.post", return_value=_resp(upstream_status)):
        with pytest.raises(HandoffRedeemError) as exc:
            redeem_accutax_handoff(CODE)
    assert exc.value.status_code == ours


def test_unreachable_upstream_is_503_and_not_retried(live_orgs):
    with patch("httpx.post", side_effect=httpx.ConnectError("down")) as post:
        with pytest.raises(HandoffRedeemError) as exc:
            redeem_accutax_handoff(CODE)
    assert exc.value.status_code == 503
    assert post.call_count == 1  # the code works once: never retry


def test_answer_without_user_id_is_refused(live_orgs):
    with patch("httpx.post", return_value=_resp(200, {"access_token": "t"})):
        with pytest.raises(HandoffRedeemError) as exc:
            redeem_accutax_handoff(CODE)
    assert exc.value.status_code == 502


# ── The route ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


def test_route_issues_our_own_session_token(client, live_orgs, monkeypatch):
    monkeypatch.setattr(routes, "fetch_accutax_accessible_orgs", lambda token, user_id: [{"id": 1}, {"id": 2}])
    with patch("httpx.post", return_value=_resp(200, UPSTREAM)):
        r = client.post("/api/v1/auth/handoff", json={"code": CODE})
    assert r.status_code == 200
    body = r.json()
    claims = jwt.decode(body["access_token"], get_jwt_secret(), algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == "11" and claims["allowed_org_ids"] == [1, 2]
    assert claims["accutax_token"] == "nest.jwt.token"
    assert body["access_token"] != "nest.jwt.token"  # the dashboard token is never handed to the browser as-is
    assert body["session_id"] == UPSTREAM["session_id"] and body["organization_id"] == 2
    assert body["name"] == "U Ser" and len(body["conversation"]) == 2
    assert [t["id"] for t in body["tenants"]] == [1, 2]


def test_route_returns_410_for_expired_code(client, live_orgs):
    with patch("httpx.post", return_value=_resp(410)):
        r = client.post("/api/v1/auth/handoff", json={"code": CODE})
    assert r.status_code == 410
    assert "expired" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["short", "A" * 129, "has space" + "A" * 20, "../../" + "A" * 20])
def test_route_rejects_malformed_code_before_calling_upstream(client, live_orgs, bad):
    with patch("httpx.post") as post:
        r = client.post("/api/v1/auth/handoff", json={"code": bad})
    assert r.status_code == 422
    post.assert_not_called()
