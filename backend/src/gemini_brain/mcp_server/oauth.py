"""The MCP server's own OAuth 2.1 authorization server, signing users in with their Accutax login.

ChatGPT and Claude register themselves (dynamic client registration), send the user to /authorize, and the
user signs in on our /login page with their Accutax email and password. The API's own login checks them.

Tokens handed to the host are opaque random strings. The Accutax AI token they stand for, and the Accutax
app token inside it, stay in this server's store, encrypted, and are what the tools forward to the API. So
OpenAI or Anthropic hold nothing that works against the Accutax API directly.

Pending sign-ins and authorization codes live in memory (minutes long; a restart only means signing in
again). Clients and tokens are kept on disk so a restart does not disconnect anyone.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from gemini_brain.api.auth import create_access_token, decode_access_token, get_jwt_secret
from gemini_brain.config.settings import settings
from gemini_brain.mcp_server.upstream import Upstream, UpstreamError
from gemini_brain.mcp_server.verifier import SCOPE, AccutaxTokenVerifier

logger = logging.getLogger("gemini_brain.mcp_server.oauth")

PENDING_SECONDS = 600
CODE_SECONDS = 300
MAX_LOGIN_ATTEMPTS = 5

_BACKEND = Path(__file__).resolve().parents[3]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    key = hashlib.sha256(b"accutax-mcp-state:" + get_jwt_secret().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class _Store:
    """A dict kept in one encrypted JSON file, rewritten whole and atomically on every change."""

    def __init__(self, path: Path, fernet: Fernet):
        self._path, self._fernet, self._lock = path, fernet, threading.Lock()
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(fernet.decrypt(path.read_bytes()))
            except (InvalidToken, ValueError):
                # A changed JWT_SECRET makes the file unreadable: every connection must sign in again.
                logger.warning("MCP state %s could not be read; starting empty", path.name)

    def get(self, key: str) -> Optional[dict[str, Any]]:
        return self._data.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def pop(self, *keys: str) -> Optional[dict[str, Any]]:
        with self._lock:
            first = None
            for i, key in enumerate(keys):
                value = self._data.pop(key, None)
                first = value if i == 0 else first
            self._save()
            return first

    def prune(self, expired: Callable[[dict[str, Any]], bool]) -> None:
        with self._lock:
            stale = [k for k, v in self._data.items() if expired(v)]
            for k in stale:
                del self._data[k]
            if stale:
                self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet.encrypt(json.dumps(self._data).encode("utf-8")))
        os.replace(tmp, self._path)


@dataclass
class _Pending:
    client_id: str
    params: AuthorizationParams
    expires_at: float
    attempts: int = 0


@dataclass
class _Code:
    code: AuthorizationCode
    identity: dict[str, Any] = field(default_factory=dict)


class LoginFailed(Exception):
    """A sign-in the page shows again with this message."""


def redirect_host_allowed(uri: str) -> bool:
    allowed = {h.strip().lower() for h in settings.mcp_redirect_hosts.split(",") if h.strip()}
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return host in allowed and parts.scheme in ("http", "https")
    return parts.scheme == "https" and host in allowed


class AccutaxOAuthProvider:
    """OAuthAuthorizationServerProvider (mcp.server.auth.provider) over the Accutax login."""

    def __init__(self, upstream: Upstream, *, issuer_url: str, state_dir: Path | str | None = None,
                 clock: Callable[[], float] = time.time):
        directory = Path(state_dir or settings.mcp_state_dir)
        if not directory.is_absolute():
            directory = _BACKEND / directory
        fernet = _fernet()
        self._clients = _Store(directory / "clients.enc", fernet)
        self._tokens = _Store(directory / "tokens.enc", fernet)
        self._pending: dict[str, _Pending] = {}
        self._codes: dict[str, _Code] = {}
        self._upstream = upstream
        self._issuer = issuer_url.rstrip("/")
        self._now = clock
        self._api_tokens = AccutaxTokenVerifier()

    # ── Clients ──
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._clients.get(client_id)
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        bad = [str(u) for u in (client_info.redirect_uris or []) if not redirect_host_allowed(str(u))]
        if bad or not client_info.redirect_uris:
            logger.info("MCP client registration refused: redirect %s", bad or "missing")
            raise RegistrationError(error="invalid_redirect_uri",
                                    error_description="This redirect URI is not allowed for the Accutax connector.")
        self._clients.put(client_info.client_id, client_info.model_dump(mode="json", exclude_none=True))
        logger.info("MCP client registered: %s (%s)", client_info.client_id, client_info.client_name or "unnamed")

    # ── Sign-in ──
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        scopes = params.scopes or [SCOPE]
        if any(s != SCOPE for s in scopes):
            raise AuthorizeError(error="invalid_scope", error_description=f"Only {SCOPE} is offered.")
        params = params.model_copy(update={"scopes": scopes})
        now = self._now()
        self._pending = {k: p for k, p in self._pending.items() if p.expires_at > now}
        txn = secrets.token_urlsafe(32)
        self._pending[txn] = _Pending(client.client_id, params, now + PENDING_SECONDS)
        return f"{self._issuer}/login?txn={txn}"

    def pending(self, txn: str) -> Optional[_Pending]:
        p = self._pending.get(txn or "")
        return p if p and p.expires_at > self._now() else None

    async def complete_login(self, txn: str, email: str, password: str) -> str:
        """Check the credentials with the API; on success, the client's redirect with a fresh code."""
        p = self.pending(txn)
        if p is None:
            raise LoginFailed("This sign-in link has expired. Start the connection again from your AI app.")
        p.attempts += 1
        if p.attempts > MAX_LOGIN_ATTEMPTS:
            self._pending.pop(txn, None)
            raise LoginFailed("Too many attempts. Start the connection again from your AI app.")
        try:
            body = await self._upstream.login(email.strip(), password)
        except UpstreamError as e:
            raise LoginFailed(str(e)) from None
        if not body or not body.get("access_token"):
            raise LoginFailed("Wrong email or password.")
        claims = decode_access_token(body["access_token"])
        identity = {
            "user_id": int(claims.get("sub") or body.get("user_id")),
            "email": claims.get("email") or body.get("email") or "",
            "allowed_org_ids": [int(o) for o in (claims.get("allowed_org_ids") or body.get("allowed_org_ids") or [])],
            "accutax_token": claims.get("accutax_token") or "",
        }
        self._pending.pop(txn, None)
        code = secrets.token_urlsafe(32)
        self._codes[code] = _Code(AuthorizationCode(
            code=code, scopes=p.params.scopes or [SCOPE], expires_at=self._now() + CODE_SECONDS,
            client_id=p.client_id, code_challenge=p.params.code_challenge, redirect_uri=p.params.redirect_uri,
            redirect_uri_provided_explicitly=p.params.redirect_uri_provided_explicitly,
            resource=p.params.resource, subject=str(identity["user_id"]),
        ), identity)
        logger.info("MCP sign-in: user %s authorized client %s", identity["user_id"], p.client_id)
        return construct_redirect_uri(str(p.params.redirect_uri), code=code, state=p.params.state)

    def deny(self, txn: str) -> Optional[str]:
        p = self._pending.pop(txn or "", None)
        if p is None:
            return None
        logger.info("MCP sign-in cancelled for client %s", p.client_id)
        return construct_redirect_uri(str(p.params.redirect_uri), error="access_denied", state=p.params.state)

    # ── Codes and tokens ──
    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        entry = self._codes.get(authorization_code)
        if entry is None or entry.code.client_id != client.client_id or entry.code.expires_at < self._now():
            return None
        return entry.code

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        entry = self._codes.pop(authorization_code.code, None)  # single use
        if entry is None:
            raise TokenError(error="invalid_grant", error_description="Authorization code already used.")
        return self._issue(client.client_id, authorization_code.scopes, entry.identity, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        rec = self._live(refresh_token, "refresh")
        if rec is None or rec["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=rec["client_id"], scopes=rec["scopes"],
                            expires_at=int(rec["exp"]), resource=rec.get("resource"),
                            subject=str(rec["identity"]["user_id"]))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        rec = self._tokens.pop(_hash(refresh_token.token))
        if rec is None:
            raise TokenError(error="invalid_grant", error_description="Refresh token already used.")
        if rec.get("pair"):
            self._tokens.pop(rec["pair"])
        wanted = scopes or rec["scopes"]
        if any(s not in rec["scopes"] for s in wanted):
            raise TokenError(error="invalid_scope", error_description="Scope wider than the original grant.")
        return self._issue(client.client_id, wanted, rec["identity"], rec.get("resource"))

    async def load_access_token(self, token: str) -> AccessToken | None:
        rec = self._live(token, "access")
        if rec is not None:
            return AccessToken(token=token, client_id=rec["client_id"], scopes=rec["scopes"],
                               expires_at=int(rec["exp"]), resource=rec.get("resource"),
                               subject=str(rec["user_id"]), claims={"upstream": rec["upstream"]})
        if settings.mcp_accept_api_tokens:
            return await self._api_tokens.verify_token(token)
        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        key = _hash(token.token)
        rec = self._tokens.get(key)
        if rec is not None:
            self._tokens.pop(key, *([rec["pair"]] if rec.get("pair") else []))

    async def exchange_identity_assertion(self, client, params) -> OAuthToken:
        raise TokenError(error="unsupported_grant_type", error_description="Not supported.")

    def _live(self, token: str, kind: str) -> Optional[dict[str, Any]]:
        rec = self._tokens.get(_hash(token or ""))
        if rec is None or rec.get("kind") != kind or rec["exp"] < self._now():
            return None
        return rec

    def _issue(self, client_id: str, scopes: list[str], identity: dict[str, Any],
               resource: Optional[str]) -> OAuthToken:
        """A new access + refresh pair, and a fresh Accutax AI token behind the access token."""
        now = self._now()
        self._tokens.prune(lambda r: r["exp"] < now)
        access_seconds = settings.mcp_access_token_minutes * 60
        refresh_seconds = settings.mcp_refresh_token_days * 86400
        upstream_jwt = create_access_token(
            identity["user_id"], identity["email"], identity["allowed_org_ids"],
            expires_delta=timedelta(seconds=access_seconds + 300), accutax_token=identity.get("accutax_token", ""),
        )
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        a_key, r_key = _hash(access), _hash(refresh)
        self._tokens.put(a_key, {"kind": "access", "client_id": client_id, "scopes": scopes, "resource": resource,
                                 "exp": now + access_seconds, "user_id": identity["user_id"],
                                 "upstream": upstream_jwt, "pair": r_key})
        self._tokens.put(r_key, {"kind": "refresh", "client_id": client_id, "scopes": scopes, "resource": resource,
                                 "exp": now + refresh_seconds, "identity": identity, "pair": a_key})
        return OAuthToken(access_token=access, expires_in=access_seconds, scope=" ".join(scopes),
                          refresh_token=refresh)


# ── The sign-in page ────────────────────────────────────────────────────────

_SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    # No form-action: browsers apply it to the redirect after the post, which goes to the AI app's callback.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in to Accutax</title>
<style>
body{{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f6f4;color:#1d1d1b}}
main{{max-width:380px;margin:8vh auto;padding:32px;background:#fff;border:1px solid #e3e2dd;border-radius:12px}}
h1{{font-size:20px;margin:0 0 8px}}p{{margin:0 0 16px;color:#55544f}}
label{{display:block;font-weight:600;margin:12px 0 4px}}
input{{width:100%;box-sizing:border-box;padding:10px;border:1px solid #cfcdc6;border-radius:8px;font:inherit}}
.row{{display:flex;flex-direction:row-reverse;gap:8px;margin-top:20px}}button{{flex:1;padding:10px;border-radius:8px;font:inherit;cursor:pointer}}
.ok{{background:#1d1d1b;color:#fff;border:0}}.no{{background:#fff;border:1px solid #cfcdc6}}
.err{{background:#fbebeb;color:#8a1c1c;padding:8px 12px;border-radius:8px}}ul{{padding-left:20px;color:#55544f}}
</style></head><body><main>
<h1>Connect {client} to Accutax</h1>
<p>{client} will be able to <b>read</b> your Accutax data and ask Accutax AI questions. It cannot change,
file or pay anything. You can disconnect it at any time.</p>
<ul><li>Returns to: {host}</li></ul>
{error}
<form method="post" action="login">
<input type="hidden" name="txn" value="{txn}">
<label for="email">Accutax email</label><input id="email" name="email" type="email" autocomplete="username" required value="{email}">
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<!-- Allow comes first in the page: Enter in a field submits with the first button. Cancel is drawn on the left. -->
<div class="row"><button class="ok" name="action" value="allow">Sign in and allow</button>
<button class="no" name="action" value="deny" formnovalidate>Cancel</button></div>
</form></main></body></html>"""

_GONE = ("<!doctype html><meta charset='utf-8'><title>Accutax</title><p style='font:15px system-ui;margin:10vh auto;"
         "max-width:380px'>This sign-in link has expired. Start the connection again from your AI app.</p>")


def _page(provider: AccutaxOAuthProvider, client: Optional[OAuthClientInformationFull], txn: str, p: _Pending,
          error: str = "", email: str = "", status_code: int = 200) -> Response:
    name = (client.client_name if client else None) or "An AI app"
    body = _PAGE.format(client=html.escape(name[:80]), host=html.escape(urlsplit(str(p.params.redirect_uri)).netloc),
                        error=f'<p class="err">{html.escape(error)}</p>' if error else "",
                        txn=html.escape(txn), email=html.escape(email[:200]))
    return HTMLResponse(body, status_code=status_code, headers=_SECURITY_HEADERS)


def register_login_routes(server, provider: AccutaxOAuthProvider) -> None:
    """GET/POST /login on the MCP server: the page /authorize redirects to."""

    @server.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        if request.method == "GET":
            txn = request.query_params.get("txn", "")
            p = provider.pending(txn)
            if p is None:
                return HTMLResponse(_GONE, status_code=400, headers=_SECURITY_HEADERS)
            return _page(provider, await provider.get_client(p.client_id), txn, p)

        form = await request.form()
        txn = str(form.get("txn") or "")
        if form.get("action") == "deny":
            target = provider.deny(txn)
            return (RedirectResponse(target, status_code=302) if target
                    else HTMLResponse(_GONE, status_code=400, headers=_SECURITY_HEADERS))
        email, password = str(form.get("email") or ""), str(form.get("password") or "")
        try:
            target = await provider.complete_login(txn, email, password)
        except LoginFailed as e:
            p = provider.pending(txn)
            if p is None:
                return HTMLResponse(_GONE, status_code=400, headers=_SECURITY_HEADERS)
            return _page(provider, await provider.get_client(p.client_id), txn, p, str(e), email, 401)
        except HTTPException:
            return HTMLResponse(_GONE, status_code=400, headers=_SECURITY_HEADERS)
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})
