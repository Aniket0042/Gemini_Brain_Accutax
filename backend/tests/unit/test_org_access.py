"""Organization access checks: empty allow-lists, missing user identity, live revocation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gemini_brain.api import auth
from gemini_brain.api.auth import (
    CurrentUser,
    authorize_org_scope,
    create_access_token,
    get_current_user,
    get_jwt_secret,
    resolve_allowed_org_ids,
)
from gemini_brain.api.app import app
from gemini_brain.api.routes import list_tenants
from gemini_brain.config.settings import settings


def _user(allowed: list[int], accutax_token: str = "") -> CurrentUser:
    return CurrentUser(user_id=7, email="u@example.com", allowed_org_ids=allowed, accutax_token=accutax_token)


def _tenant(oid: int) -> dict:
    return {
        "id": oid, "name": f"Org {oid}", "display_name": f"Org {oid}", "tag": "Owner",
        "badge_color": "emerald", "industry": "", "currency": "AED", "description": "",
    }


# ── authorize_org_scope ──────────────────────────────────────────────────────

def _scope(requested, allowed):
    return authorize_org_scope(requested, _user(allowed), action="test")


def _status(requested, allowed) -> int:
    with pytest.raises(HTTPException) as exc:
        _scope(requested, allowed)
    return exc.value.status_code


def test_empty_allow_list_denies_every_org():
    assert _status([5], []) == 403


def test_org_outside_allow_list_denied():
    assert _status([9], [5, 6]) == 403


def test_one_denied_org_refuses_the_whole_request():
    assert _status([5, 9], [5, 6]) == 403


def test_allowed_orgs_returned_deduplicated_in_request_order():
    assert _scope([6, 5, 6], [5, 6]) == [6, 5]


def test_no_org_named_returns_empty_scope():
    assert _scope(None, []) == []
    assert _scope([None], [5]) == []


def test_non_integer_org_id_rejected():
    assert _status(["abc"], [5]) == 400


def test_too_many_orgs_rejected(monkeypatch):
    monkeypatch.setattr(auth.settings, "max_orgs_per_query", 2)
    assert _status([1, 2, 3], [1, 2, 3]) == 400


def test_decisions_are_audit_logged(caplog):
    caplog.set_level("INFO", logger="gemini_brain.audit.org_access")
    _scope([5], [5])
    with pytest.raises(HTTPException):
        _scope([9], [5])
    messages = [r.getMessage() for r in caplog.records]
    assert any("granted" in m and "orgs=[5]" in m for m in messages)
    assert any("denied" in m and "denied=[9]" in m for m in messages)


# ── Routes refuse before any work starts ─────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


def _bearer(allowed: list[int]) -> dict:
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=allowed)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/api/v1/query", "/api/v1/query/stream", "/api/v1/query/all"])
def test_query_routes_refuse_unassigned_org_without_running(client, path):
    with patch("gemini_brain.api.routes.GeminiBrainRunner") as runner:
        resp = client.post(path, json={"query": "revenue", "organization_id": 9}, headers=_bearer([5]))
    assert resp.status_code == 403
    runner.assert_not_called()


def test_session_list_refuses_unassigned_org(client):
    resp = client.get("/api/v1/sessions", params={"organization_id": 9}, headers=_bearer([5]))
    assert resp.status_code == 403


# ── get_current_user: identity ───────────────────────────────────────────────

def _signed(payload: dict) -> str:
    now = datetime.now(timezone.utc)
    body = {"iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=5)).timestamp()), **payload}
    return jwt.encode(body, get_jwt_secret(), algorithm=settings.jwt_algorithm)


def test_token_without_user_id_rejected_not_defaulted():
    token = _signed({"email": "x@example.com", "allowed_org_ids": [5]})
    with pytest.raises(HTTPException) as exc:
        get_current_user(token)
    assert exc.value.status_code == 401


def test_token_with_non_numeric_user_id_rejected():
    token = _signed({"sub": "abc", "allowed_org_ids": [5]})
    with pytest.raises(HTTPException) as exc:
        get_current_user(token)
    assert exc.value.status_code == 401


def test_upstream_login_without_user_id_is_refused():
    upstream = jwt.encode({"email": "x@example.com"}, "someone-elses-secret", algorithm="HS256")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"token": upstream}
    with patch("httpx.post", return_value=resp):
        assert auth.authenticate_with_accutax_api("x@example.com", "pw") is None


# ── get_current_user: live organization access ───────────────────────────────

def test_revoked_org_is_dropped_before_token_expires(monkeypatch):
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="": [5])
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[5, 6])
    assert get_current_user(token).allowed_org_ids == [5]


def test_live_empty_list_is_final_not_replaced_by_token_claim(monkeypatch):
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="": [])
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[5, 6])
    assert get_current_user(token).allowed_org_ids == []


def test_token_claim_used_only_when_live_lookup_unavailable():
    # conftest makes both live sources report "unavailable".
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[5, 6])
    assert get_current_user(token).allowed_org_ids == [5, 6]


def test_no_live_answer_and_no_claim_refuses_access():
    with pytest.raises(HTTPException) as exc:
        resolve_allowed_org_ids(7, "", None)
    assert exc.value.status_code == 503


def test_accutax_list_is_preferred_over_database(monkeypatch):
    db = MagicMock(return_value=[1, 2, 3])
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", db)
    monkeypatch.setattr(auth, "_fetch_accutax_orgs", lambda token, user_id: [_tenant(8)])
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[5], accutax_token="up")
    user = get_current_user(token)
    assert user.allowed_org_ids == [8]
    assert user.accutax_token == "up"
    db.assert_not_called()


def test_database_used_when_accutax_unreachable(monkeypatch):
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="": [4])
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[5], accutax_token="up")
    assert get_current_user(token).allowed_org_ids == [4]


def test_local_token_never_calls_accutax(monkeypatch):
    fetch = MagicMock(return_value=[_tenant(8)])
    monkeypatch.setattr(auth, "_fetch_accutax_orgs", fetch)
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="": [4])
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=[4])
    assert get_current_user(token).allowed_org_ids == [4]
    fetch.assert_not_called()


# ── /tenants ─────────────────────────────────────────────────────────────────

def test_tenant_dropdown_never_offers_an_org_outside_allow_list(monkeypatch):
    monkeypatch.setattr(auth, "_fetch_accutax_orgs", lambda token, user_id: [_tenant(5), _tenant(9)])
    resp = list_tenants(_user([5], accutax_token="up"))
    assert [t.id for t in resp.tenants] == [5]


# ── organization_ids contract ────────────────────────────────────────────────

def _runner_org(client, body: dict, allowed: list[int]):
    """POST /query and return (status, organization_id the runner received)."""
    with patch("gemini_brain.api.routes.GeminiBrainRunner") as runner_cls:
        runner_cls.return_value.run.return_value = {"answer": "ok"}
        resp = client.post("/api/v1/query", json={"query": "revenue", **body}, headers=_bearer(allowed))
    calls = runner_cls.return_value.run.call_args_list
    return resp.status_code, (calls[0].kwargs["organization_id"] if calls else "not called")


def test_single_org_in_list_runs_like_organization_id(client):
    assert _runner_org(client, {"organization_ids": [5]}, [5, 6]) == (200, 5)


def test_legacy_organization_id_still_works(client):
    assert _runner_org(client, {"organization_id": 6}, [5, 6]) == (200, 6)


def test_no_org_named_leaves_default_to_runner(client):
    assert _runner_org(client, {}, [5, 6]) == (200, None)


def test_duplicate_ids_collapse_to_one_org(client):
    assert _runner_org(client, {"organization_ids": [5, 5]}, [5]) == (200, 5)


def test_unassigned_org_in_list_refuses_request(client):
    assert _runner_org(client, {"organization_ids": [5, 9]}, [5, 6]) == (403, "not called")


def test_empty_org_list_rejected(client):
    assert _runner_org(client, {"organization_ids": []}, [5])[0] == 422


def test_organization_id_outside_list_rejected(client):
    assert _runner_org(client, {"organization_id": 6, "organization_ids": [5]}, [5, 6])[0] == 422


def test_several_orgs_refused_while_flag_off(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", False)
    assert _runner_org(client, {"organization_ids": [5, 6]}, [5, 6]) == (400, "not called")


def test_several_orgs_run_once_per_org_when_flag_on(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", True)
    with patch("gemini_brain.api.routes.GeminiBrainRunner") as runner_cls,          patch("gemini_brain.api.routes._org_meta", lambda user, orgs: {o: {} for o in orgs}):
        runner_cls.return_value.run.return_value = {"answer": "ok"}
        resp = client.post("/api/v1/query", json={"query": "revenue", "organization_ids": [5, 6]},
                           headers=_bearer([5, 6]))
    assert resp.status_code == 200
    ran = sorted(c.kwargs["organization_id"] for c in runner_cls.return_value.run.call_args_list)
    assert ran == [5, 6]


def test_unassigned_org_is_403_even_with_flag_off(client, monkeypatch):
    # Authorization runs before the feature check, so a probe learns nothing new.
    monkeypatch.setattr(auth.settings, "multi_org_enabled", False)
    assert _runner_org(client, {"organization_ids": [5, 9]}, [5]) == (403, "not called")


def test_tenants_response_advertises_multi_org_limits(monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", True)
    monkeypatch.setattr(auth.settings, "max_orgs_per_query", 4)
    resp = list_tenants(_user([5]))
    assert resp.multi_org_enabled is True
    assert resp.max_orgs_per_query == 4
