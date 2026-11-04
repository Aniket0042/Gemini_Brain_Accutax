import hashlib
import logging
import threading
from dataclasses import dataclass

import httpx
import jwt
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gemini_brain.insights.config import get_settings

logger = logging.getLogger("insight_engine.auth")

# A proper OpenAPI security scheme (not just a raw Header() param) -- this is
# what makes Swagger UI show a padlock icon and an "Authorize" button, so a
# token can be entered once and gets attached to every "Try it out" call
# automatically, instead of requiring a manually-typed header per request.
_bearer_scheme = HTTPBearer(auto_error=False, scheme_name="AccutaxBearer", description="Paste the Accutax JWT here (no 'Bearer ' prefix needed).")


@dataclass
class AuthContext:
    user_id: int
    organization_ids: list[int]
    email: str | None = None
    role: str | None = None


def _unverified_user_id_hint(token: str) -> int | None:
    """
    Reads the userId claim WITHOUT verifying the signature -- this service
    has no way to verify it locally (see Settings docstring). The value is
    only ever used to shape the GET /auth/profile?user_id=... URL below; it
    is never trusted as fact. If it's wrong or forged, the real backend's own
    JwtAuthGuard either rejects the token outright (expired/invalid
    signature) or its controller rejects the user_id/requester mismatch
    (ForbiddenException) -- either way introspection fails closed.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return None
    raw = payload.get("userId")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


_introspection_cache: TTLCache = TTLCache(maxsize=2000, ttl=get_settings().auth_cache_ttl_seconds)
_cache_lock = threading.Lock()
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        settings = get_settings()
        _http_client = httpx.AsyncClient(
            base_url=settings.accutax_base_url,
            timeout=httpx.Timeout(settings.auth_http_timeout_seconds, connect=2.0),
        )
    return _http_client


def _token_cache_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthContext:
    """
    Establishes who is calling and which organizations they may act on, by
    introspecting the token against the real Accutax backend's
    GET /auth/all_organizations (JwtAuthGuard-enforced -- verified live, see
    below) rather than verifying a signature we don't have the secret for. A
    200 response proves the token is genuine -- Accutax itself validated it
    -- and its body gives the organizations that user actually belongs to,
    which is what request org membership is checked against downstream.
    Results are cached briefly per-token to avoid a network round-trip on
    every insight click.

    Endpoint choice matters here: GET /auth/profile looked like a better fit
    (returns organizations directly under one call) and IS guarded in this
    checkout's source (auth.controller.ts declares @UseGuards(JwtAuthGuard)
    on it) -- but live-testing against the actual deployed backend
    (13.127.157.108:8081) showed it returns 200 with real profile/org data
    with NO Authorization header at all, while /report/* and /dashboard/*
    correctly 401. So does GET /auth/organization. The deployed backend is
    evidently running older code than this checkout for at least those two
    routes -- a real, currently-exploitable unauthenticated-disclosure gap,
    unrelated to Insight_Engine, that should be fixed and redeployed on
    accutax_bk_backend itself. GET /auth/all_organizations was verified live
    to correctly return 401 with no token, so it's used here instead -- it's
    also the exact endpoint the real frontend already calls for org data.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = credentials.credentials
    cache_key = _token_cache_key(token)

    with _cache_lock:
        cached = _introspection_cache.get(cache_key)
    if cached is not None:
        return cached

    user_id_hint = _unverified_user_id_hint(token)
    if user_id_hint is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    client = _get_http_client()
    try:
        response = await client.get(
            "/auth/all_organizations",
            params={"user_id": user_id_hint},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        logger.error("Accutax auth introspection request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify authentication with the Accutax backend. Try again shortly.",
        )

    if response.status_code != 200:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    body = response.json()
    if body.get("error"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    organizations = body.get("data") or []
    organization_ids = [org["id"] for org in organizations if isinstance(org, dict) and "id" in org]

    context = AuthContext(
        user_id=user_id_hint,
        organization_ids=organization_ids,
        email=None,
        role=None,
    )

    with _cache_lock:
        _introspection_cache[cache_key] = context

    return context


async def get_insight_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthContext:
    """Who is asking, and for which organizations. Gateway mode (AI_AUTH_MODE=gateway): the Accutax API gateway
    already checked the session, so ask it like every other AI route does. Otherwise: the Accutax JWT the
    dashboard sends, introspected live (get_auth_context)."""
    from gemini_brain.config.settings import settings as ai_settings
    if ai_settings.ai_auth_mode == "gateway":
        from gemini_brain.api.auth import get_current_user
        user = await run_in_threadpool(get_current_user, None, request)
        return AuthContext(user_id=user.user_id, organization_ids=list(user.allowed_org_ids), email=user.email or None)
    return await get_auth_context(credentials)
