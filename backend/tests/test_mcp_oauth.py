"""
test_mcp_oauth.py — the MCP server's OAuth: registration, Accutax sign-in, code and token exchange, refresh,
revocation, and what the host ends up holding.

Runs the real Starlette app of the MCP server with the real FastAPI app behind it (in-process); only the
upstream Accutax login and live org lookups are faked.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from gemini_brain.api.app import create_app
from gemini_brain.api.auth import decode_access_token
from gemini_brain.mcp_server.oauth import AccutaxOAuthProvider
from gemini_brain.mcp_server.server import build_server
from gemini_brain.mcp_server.upstream import Upstream

ISSUER = "http://localhost:8012"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "x" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def api_app():
    with patch("gemini_brain.api.auth.init_auth_db"):
        yield create_app()


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    """Accutax login knows one user (101, orgs 24 and 25); no live org lookups."""
    from gemini_brain.api import auth, routes

    def upstream_login(email, password):
        if email == "owner@example.com" and password == "right":
            return {"access_token": "accutax-app-token", "user_id": 101, "email": email, "allowed_org_ids": [24, 25]}
        return None

    monkeypatch.setattr(routes, "authenticate_with_accutax_api", upstream_login)
    monkeypatch.setattr(routes, "get_user_by_email", lambda email: None)
    monkeypatch.setattr(auth, "_fetch_accutax_orgs", lambda token, user_id: None)
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="", **_: None)
    monkeypatch.setattr(routes, "_resolve_tenants_for_org_ids",
                        lambda ids: [{"id": i, "name": f"Org {i}", "display_name": f"Org {i}"} for i in ids])
    auth._DB_ORG_CACHE.clear()
    auth._ACCUTAX_ORG_CACHE.clear()


@pytest.fixture
def setup(api_app, tmp_path):
    upstream = Upstream("http://api", transport=httpx.ASGITransport(app=api_app))
    provider = AccutaxOAuthProvider(upstream, issuer_url=ISSUER, state_dir=tmp_path)
    app = build_server(upstream, provider).streamable_http_app(stateless_http=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as client:
        yield client, provider, tmp_path


def _register(client, redirect=CLAUDE_CALLBACK):
    return client.post("/register", json={"client_name": "Claude", "redirect_uris": [redirect],
                                          "token_endpoint_auth_method": "none",
                                          "grant_types": ["authorization_code", "refresh_token"],
                                          "response_types": ["code"]})


def _authorize(client, client_id) -> str:
    resp = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CALLBACK, "state": "st8",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256", "scope": "accutax.read"})
    assert resp.status_code == 302, resp.text
    return parse_qs(urlsplit(resp.headers["location"]).query)["txn"][0]


def _sign_in(client, txn, password="right"):
    return client.post("/login", data={"txn": txn, "email": "owner@example.com", "password": password,
                                       "action": "allow"})


def _connect(client) -> tuple[str, dict]:
    client_id = _register(client).json()["client_id"]
    resp = _sign_in(client, _authorize(client, client_id))
    code = parse_qs(urlsplit(resp.headers["location"]).query)["code"][0]
    tokens = client.post("/token", data={"grant_type": "authorization_code", "code": code, "client_id": client_id,
                                         "redirect_uri": CLAUDE_CALLBACK, "code_verifier": VERIFIER})
    assert tokens.status_code == 200, tokens.text
    return client_id, tokens.json()


def _tools_list(client, token):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"})


class TestDiscoveryAndRegistration:

    def test_metadata_advertises_registration_and_pkce(self, setup):
        client, _, _ = setup
        meta = client.get("/.well-known/oauth-authorization-server").json()
        assert meta["registration_endpoint"].endswith("/register")
        assert meta["code_challenge_methods_supported"] == ["S256"]
        prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["authorization_servers"][0].rstrip("/") == ISSUER

    def test_claude_and_chatgpt_callbacks_accepted(self, setup):
        client, _, _ = setup
        assert _register(client).status_code == 201
        assert _register(client, "https://chatgpt.com/connector_platform_oauth_redirect").status_code == 201

    @pytest.mark.parametrize("redirect", ["https://evil.example.com/cb", "http://claude.ai/cb",
                                          "https://claude.ai.evil.com/cb"])
    def test_other_redirects_refused(self, setup, redirect):
        client, _, _ = setup
        resp = _register(client, redirect)
        assert resp.status_code == 400 and resp.json()["error"] == "invalid_redirect_uri"


class TestSignIn:

    def test_login_page_names_client_and_return_host(self, setup):
        client, _, _ = setup
        client_id = _register(client).json()["client_id"]
        page = client.get(f"/login?txn={_authorize(client, client_id)}")
        assert page.status_code == 200 and "Connect Claude to Accutax" in page.text and "claude.ai" in page.text
        assert page.headers["x-frame-options"] == "DENY"

    def test_client_name_is_escaped(self, setup):
        client, _, _ = setup
        reg = client.post("/register", json={"client_name": "<script>x</script>", "redirect_uris": [CLAUDE_CALLBACK],
                                             "token_endpoint_auth_method": "none"}).json()
        page = client.get(f"/login?txn={_authorize(client, reg['client_id'])}")
        assert "<script>x" not in page.text and "&lt;script&gt;" in page.text

    def test_wrong_password_shows_page_again(self, setup):
        client, _, _ = setup
        txn = _authorize(client, _register(client).json()["client_id"])
        resp = _sign_in(client, txn, "wrong")
        assert resp.status_code == 401 and "Wrong email or password" in resp.text
        assert _sign_in(client, txn).status_code == 302  # same link still works after one mistake

    def test_attempts_are_capped(self, setup):
        client, _, _ = setup
        txn = _authorize(client, _register(client).json()["client_id"])
        for _ in range(5):
            _sign_in(client, txn, "wrong")
        assert _sign_in(client, txn).status_code == 400  # link spent, even with the right password

    def test_cancel_returns_access_denied(self, setup):
        client, _, _ = setup
        txn = _authorize(client, _register(client).json()["client_id"])
        resp = client.post("/login", data={"txn": txn, "action": "deny"})
        q = parse_qs(urlsplit(resp.headers["location"]).query)
        assert q["error"] == ["access_denied"] and q["state"] == ["st8"]

    def test_unknown_link(self, setup):
        client, _, _ = setup
        assert client.get("/login?txn=nope").status_code == 400


class TestTokens:

    def test_host_gets_opaque_tokens_that_open_mcp(self, setup):
        client, provider, _ = setup
        _, tokens = _connect(client)
        assert "." not in tokens["access_token"] and "." not in tokens["refresh_token"]  # not JWTs
        assert tokens["scope"] == "accutax.read"
        assert _tools_list(client, tokens["access_token"]).status_code == 200
        access = asyncio.run(provider.load_access_token(tokens["access_token"]))
        claims = decode_access_token(access.claims["upstream"])
        assert claims["sub"] == "101" and claims["accutax_token"] == "accutax-app-token"

    def test_code_is_single_use_and_needs_verifier(self, setup):
        client, _, _ = setup
        client_id = _register(client).json()["client_id"]
        resp = _sign_in(client, _authorize(client, client_id))
        code = parse_qs(urlsplit(resp.headers["location"]).query)["code"][0]
        base = {"grant_type": "authorization_code", "code": code, "client_id": client_id,
                "redirect_uri": CLAUDE_CALLBACK}
        assert client.post("/token", data={**base, "code_verifier": "y" * 64}).status_code == 400
        assert client.post("/token", data={**base, "code_verifier": VERIFIER}).status_code == 200
        assert client.post("/token", data={**base, "code_verifier": VERIFIER}).status_code == 400

    def test_refresh_rotates_and_retires_old_pair(self, setup):
        client, _, _ = setup
        client_id, first = _connect(client)
        resp = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"],
                                           "client_id": client_id})
        assert resp.status_code == 200, resp.text
        second = resp.json()
        assert second["access_token"] != first["access_token"]
        assert _tools_list(client, first["access_token"]).status_code == 401
        assert _tools_list(client, second["access_token"]).status_code == 200
        again = client.post("/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"],
                                            "client_id": client_id})
        assert again.status_code == 400

    def test_revoke_ends_the_connection(self, setup):
        client, _, _ = setup
        client_id, tokens = _connect(client)
        # The SDK's revocation form requires the client_secret field, empty for a public client.
        resp = client.post("/revoke", data={"token": tokens["refresh_token"], "client_id": client_id,
                                            "client_secret": ""})
        assert resp.status_code == 200, resp.text
        assert _tools_list(client, tokens["access_token"]).status_code == 401

    def test_expired_access_token_refused(self, setup):
        client, provider, _ = setup
        _, tokens = _connect(client)
        provider._now = lambda: 10 ** 11
        assert asyncio.run(provider.load_access_token(tokens["access_token"])) is None

    def test_state_survives_restart_and_is_encrypted(self, setup, api_app):
        client, _, state_dir = setup
        _, tokens = _connect(client)
        raw = (state_dir / "tokens.enc").read_bytes()
        assert b"accutax-app-token" not in raw and b"owner@example.com" not in raw
        reborn = AccutaxOAuthProvider(Upstream("http://api"), issuer_url=ISSUER, state_dir=state_dir)
        assert asyncio.run(reborn.load_access_token(tokens["access_token"])) is not None

    def test_pasted_api_token_can_be_switched_off(self, setup, monkeypatch):
        from gemini_brain.api.auth import create_access_token
        from gemini_brain.config.settings import settings
        client, _, _ = setup
        pasted = create_access_token(101, "owner@example.com", [24])
        assert _tools_list(client, pasted).status_code == 200
        monkeypatch.setattr(settings, "mcp_accept_api_tokens", False)
        assert _tools_list(client, pasted).status_code == 401
