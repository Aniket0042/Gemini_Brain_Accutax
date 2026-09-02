"""
service_token.py — Auto-refreshing service-account token for unattended API calls.

`settings.accutax_auth_token` is a long-lived seed: a ~24h JWT pasted into the
environment. It goes stale, and when it does every Accutax REST call returns
HTTP 401. In an interactive request that is masked — the caller's own token
arrives on the request and wins. Everything unattended has no such token and
degrades silently to the SQL fallback tier: scheduled jobs, empty-result SQL
verification, and the latency harness.

When ACCUTAX_SERVICE_EMAIL / ACCUTAX_SERVICE_PASSWORD are configured, this
module re-authenticates against the backend's own /auth/login shortly before
expiry and caches the result in memory. If the service account is not
configured, or a refresh attempt fails, it falls back to whatever is cached or
to the static seed token — so behaviour is unchanged from before in that case.

**Precedence, and why it matters:** this is only ever the *last* tier. Callers
resolve a token as:

    explicit auth_token  >  active_auth_token ContextVar (the real user)  >  get_service_token()

A service token must never stand in for a user token. It may carry different
tenant access than the caller, so substituting it would both misattribute the
request and widen what the request can reach.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

import httpx

from gemini_brain.auth.token_monitor import inspect_jwt_token
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.auth.service_token")

#: Refresh once fewer than this many seconds remain on the cached token.
REFRESH_MARGIN_SECONDS = 3600

#: After a failed refresh, wait this long before trying again — a login endpoint
#: that is down or rejecting credentials must not be retried on every API call.
REFRESH_RETRY_BACKOFF_SECONDS = 300

#: Login is on the critical path of the first unattended call; keep it short.
LOGIN_TIMEOUT_SECONDS = 8.0

_lock = threading.Lock()
_cache: Dict[str, Any] = {"token": None, "exp": None, "last_failed_attempt": None}


def _token_exp(token: str) -> Optional[float]:
    """Expiry of a JWT, or None if it has no decodable exp claim."""
    health = inspect_jwt_token(token)
    return health.expires_at


def _fetch_fresh_token() -> Optional[str]:
    """Log in with the configured service account. Returns None on any failure."""
    email = settings.accutax_service_email
    password = settings.accutax_service_password
    if not (email and password):
        return None

    url = f"{settings.accutax_base_url.rstrip('/')}/auth/login"
    try:
        resp = httpx.post(
            url,
            json={"email": email, "password": password},
            timeout=LOGIN_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        logger.error("Accutax service-account login request failed: %s", e)
        return None

    if resp.status_code not in (200, 201):
        logger.error(
            "Accutax service-account login failed: HTTP %d %s",
            resp.status_code, resp.text[:200],
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        logger.error("Accutax service-account login returned non-JSON body")
        return None

    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    token = data.get("token") or data.get("access_token") or nested.get("token")
    if not token:
        logger.error(
            "Accutax service-account login response had no token field: %s",
            str(data)[:200],
        )
        return None

    logger.info("Accutax service-account token refreshed (user=%s)", email)
    return token


def get_service_token() -> str:
    """Return a non-expired bearer token for unattended calls.

    Refreshes via the service account when the cached token is missing,
    undecodable, or near expiry. Never raises: on failure it returns the cached
    token (possibly stale) or the static seed, matching prior behaviour.
    """
    configured = bool(settings.accutax_service_email and settings.accutax_service_password)

    with _lock:
        token = _cache["token"] or settings.accutax_auth_token or None

        if not configured:
            # Nothing to refresh with. Return the seed as-is rather than recording
            # a "failed attempt" for something that was never attempted — that
            # would log a misleading refresh warning on a deployment that simply
            # hasn't opted into the service account.
            return token or ""

        exp = _cache["exp"]
        if exp is None and token:
            exp = _token_exp(token)
            _cache["exp"] = exp

        needs_refresh = (
            not token
            or exp is None
            or (exp - time.time()) < REFRESH_MARGIN_SECONDS
        )
        last_failed = _cache["last_failed_attempt"]
        recently_failed = (
            last_failed is not None
            and (time.time() - last_failed) < REFRESH_RETRY_BACKOFF_SECONDS
        )

        if needs_refresh and not recently_failed:
            fresh = _fetch_fresh_token()
            if fresh:
                _cache["token"] = fresh
                _cache["exp"] = _token_exp(fresh)
                _cache["last_failed_attempt"] = None
                return fresh

            _cache["last_failed_attempt"] = time.time()
            if token:
                logger.warning(
                    "Accutax token refresh unavailable/failed — reusing cached token "
                    "(may be stale; REST calls may return 401)."
                )
            else:
                logger.error(
                    "No Accutax token available (no cache, no static seed, refresh failed)."
                )

        return token or ""


def reset_cache() -> None:
    """Clear the cached token. For tests and for forcing a refresh after a 401."""
    with _lock:
        _cache["token"] = None
        _cache["exp"] = None
        _cache["last_failed_attempt"] = None
