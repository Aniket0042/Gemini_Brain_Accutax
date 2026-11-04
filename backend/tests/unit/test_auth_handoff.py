"""Tests for POST /api/v1/auth/handoff (one-time dashboard code exchange)."""
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token, decode_access_token

client = TestClient(app)
CODE = "A" * 43


def _token(user_id=5):
    return create_access_token(user_id=user_id, email="u@example.com", allowed_org_ids=[1, 2])


def _body(**over):
    body = {
        "access_token": _token(),
        "user_id": 5,
        "email": "u@example.com",
        "name": "U Ser",
        "organization_id": 1,
        "session_id": "11111111-1111-4111-8111-111111111111",
        "current_page": "/invoices",
        "conversation": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    }
    body.update(over)
    return body


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def _post(resp=None, exc=None, code=CODE, orgs=(1, 2)):
    def fake_post(url, **kw):
        fake_post.calls.append((url, kw))
        if exc:
            raise exc
        return resp
    fake_post.calls = []
    with patch("httpx.post", fake_post), \
         patch("gemini_brain.api.routes._live_allowed_org_ids", return_value=list(orgs)), \
         patch("gemini_brain.api.routes.fetch_accutax_accessible_orgs", return_value=[{"id": o} for o in orgs]):
        r = client.post("/api/v1/auth/handoff", json={"code": code})
    return r, fake_post.calls


def test_handoff_success():
    r, calls = _post(_Resp(200, _body()))
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == 5 and data["allowed_org_ids"] == [1, 2]
    assert decode_access_token(data["access_token"])["sub"] == "5"
    assert data["handoff"]["organization_id"] == 1
    assert data["handoff"]["session_id"] == "11111111-1111-4111-8111-111111111111"
    assert data["handoff"]["current_page"] == "/invoices"
    assert len(data["handoff"]["conversation"]) == 2
    url, kw = calls[0]
    assert url.endswith("/handoff/exchange")
    assert kw["json"] == {"code": CODE, "audience": "ai-chat"}
    assert kw["timeout"].read == 5.0


def test_handoff_service_key_header_only_when_set():
    from gemini_brain.config.settings import settings
    with patch.object(settings, "handoff_service_key", "k"):
        _, calls = _post(_Resp(200, _body()))
    assert calls[0][1]["headers"] == {"X-Handoff-Service-Key": "k"}
    with patch.object(settings, "handoff_service_key", None):
        _, calls = _post(_Resp(200, _body()))
    assert calls[0][1]["headers"] == {}


def test_handoff_org_not_allowed_is_dropped():
    r, _ = _post(_Resp(200, _body(organization_id=99)))
    assert r.status_code == 200
    assert r.json()["handoff"]["organization_id"] is None


def test_handoff_bad_code_format():
    for bad in ("short", "a" * 129, "has space " + "a" * 20, "bad/char" + "a" * 20):
        r, calls = _post(_Resp(200, _body()), code=bad)
        assert r.status_code == 422
        assert calls == []


def test_handoff_expired_code():
    for status in (404, 410):
        r, _ = _post(_Resp(status, {}))
        assert r.status_code == 401
        assert r.json()["detail"] == "Handoff link expired. Go back to the dashboard and try again."


def test_handoff_user_id_mismatch():
    r, _ = _post(_Resp(200, _body(user_id=6)))
    assert r.status_code == 401


def test_handoff_untrusted_token_rejected():
    r, _ = _post(_Resp(200, _body(access_token="not.a.jwt")))
    assert r.status_code == 401


def test_handoff_unreachable():
    r, _ = _post(exc=httpx.ConnectError("boom"))
    assert r.status_code == 503
