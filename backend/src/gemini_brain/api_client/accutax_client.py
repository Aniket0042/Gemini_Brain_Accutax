"""
accutax_client.py — High-performance HTTP client for calling the Accutax REST API.

Phase 3 upgrade + Resilience hardening:
- Uses connection-pooled httpx.AsyncClient / Client with keepalive.
- Lowers timeout from 8.0s to 6.0s (connect timeout 2.0s).
- call_api_resilient returns structured Outcome / Retrieved instances.
- extract_data_safe correctly keeps error envelopes wrapped.
- Preserves backwards-compatible call_api, call_api_async, extract_data shims.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple

import httpx

from gemini_brain.auth.service_token import get_service_token
from gemini_brain.config.settings import settings
from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload

logger = logging.getLogger("gemini_brain.api_client.accutax_client")

# ContextVar for request-scoped dynamic Accutax bearer tokens
active_auth_token: ContextVar[str] = ContextVar("active_auth_token", default="")


def _is_route_not_found(body_text: str) -> bool:
    """True when a 404 body is the backend framework's own "no route matches
    this path" response (Express/Nest's default `Cannot GET /x`), as opposed
    to an application-level "no data for this tenant" signal.

    Confirmed by cross-checking the live OpenAPI spec at /api-json: several
    registered tools (trial_balance, vendor_balance_summary, and others) point
    at paths that were never actually deployed, and 404 identically for both
    empty and data-rich organizations with this exact framework-default body —
    proving it means "this endpoint doesn't exist", not "no data here".
    """
    if not body_text:
        return False
    try:
        data = json.loads(body_text)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    message = str(data.get("message", ""))
    return message.startswith(("Cannot GET", "Cannot POST", "Cannot PUT", "Cannot DELETE", "Cannot PATCH"))

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3

# Shared connection-pooled AsyncClient
_async_client: Optional[httpx.AsyncClient] = None
_sync_client: Optional[httpx.Client] = None


def get_async_client(base_url: str = "", auth_token: str = "") -> httpx.AsyncClient:
    """Get or create singleton httpx.AsyncClient with connection pooling."""
    global _async_client
    b_url = base_url or settings.accutax_base_url
    token = auth_token or active_auth_token.get() or get_service_token()

    if _async_client is None or _async_client.is_closed:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/json"

        _async_client = httpx.AsyncClient(
            base_url=b_url,
            timeout=httpx.Timeout(6.0, connect=2.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            headers=headers,
        )
    return _async_client


def get_sync_client(base_url: str = "", auth_token: str = "") -> httpx.Client:
    """Get or create singleton httpx.Client for synchronous calls."""
    global _sync_client
    b_url = base_url or settings.accutax_base_url
    token = auth_token or active_auth_token.get() or get_service_token()

    if _sync_client is None or _sync_client.is_closed:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/json"

        _sync_client = httpx.Client(
            base_url=b_url,
            timeout=httpx.Timeout(6.0, connect=2.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            headers=headers,
        )
    return _sync_client


async def close_client_async() -> None:
    """Gracefully close the async client on application shutdown."""
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None


def close_client() -> None:
    """Gracefully close the sync client on application shutdown."""
    global _sync_client
    if _sync_client is not None and not _sync_client.is_closed:
        _sync_client.close()
        _sync_client = None


def _format_url_path(endpoint: str, path_params: Dict[str, Any]) -> str:
    """Format path parameters into endpoint URL string."""
    url_path = endpoint
    for key, val in path_params.items():
        url_path = url_path.replace(f":{key}", str(val))
        url_path = url_path.replace(f"{{{key}}}", str(val))
    return url_path


def extract_data_safe(raw: Any) -> Tuple[Any, str]:
    """Unwrap Accutax envelopes. Returns (payload, note).

    Unlike the old extract_data, an explicit `success: false` envelope is
    returned AS the envelope so classify_payload can mark it INVALID —
    it is never mistaken for the data itself.
    """
    if not isinstance(raw, dict):
        return raw, ""

    if raw.get("success") is False:
        return raw, "upstream_success_false"

    if "data" in raw:
        return raw["data"], ""
    if "details" in raw:
        return raw["details"], ""
    if "results" in raw:
        return raw["results"], ""

    if "success" in raw and len(raw) == 2:
        for k, v in raw.items():
            if k != "success":
                # Only unwrap containers. A bare string/number beside `success`
                # is a message, not a dataset.
                if isinstance(v, (list, dict)):
                    return v, ""
                return raw, "scalar_beside_success"
    return raw, ""


def extract_data(raw: Any) -> Any:
    """DEPRECATED — kept for backwards compatibility. Use extract_data_safe."""
    payload, _ = extract_data_safe(raw)
    return payload


def call_api_resilient(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    *,
    base_url: str = "",
    auth_token: str = "",
    timeout: float = 6.0,
    attempts: int = _MAX_ATTEMPTS,
) -> Retrieved:
    """GET with bounded retry + jitter, returning an explicit Retrieved outcome.

    Never raises. Never returns None.
    """
    client = get_sync_client(base_url, auth_token)
    url_path = _format_url_path(endpoint, path_params)
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    headers = {}
    token = auth_token or active_auth_token.get() or get_service_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_reason, last_detail, last_status = "unknown", "", None

    for attempt in range(1, attempts + 1):
        try:
            resp = client.get(url_path, params=clean_params, headers=headers, timeout=timeout)
            last_status = resp.status_code

            if resp.status_code == 403:
                return Retrieved(
                    Outcome.DENIED,
                    tier="live_api",
                    endpoint=endpoint,
                    reason="http_403",
                    http_status=403,
                    detail=resp.text[:300],
                )

            if resp.status_code == 401:
                return Retrieved(
                    Outcome.UNAVAILABLE,
                    tier="live_api",
                    endpoint=endpoint,
                    reason="http_401_token_expired",
                    http_status=401,
                    detail=resp.text[:300],
                )

            if resp.status_code == 404:
                if _is_route_not_found(resp.text):
                    # The backend has no route handler for this path at all —
                    # a broken/undeployed endpoint, not "no data for this
                    # tenant". Treat as UNAVAILABLE so it falls through to the
                    # SQL fallback tier instead of confidently telling the
                    # user "confirmed zero records" for something that was
                    # never actually queryable.
                    return Retrieved(
                        Outcome.UNAVAILABLE,
                        tier="live_api",
                        endpoint=endpoint,
                        reason="http_404_route_not_found",
                        http_status=404,
                        detail=resp.text[:300],
                    )
                # Upstream says: this resource does not exist for this tenant.
                # That is EMPTY, not a crash.
                return Retrieved(
                    Outcome.EMPTY,
                    tier="live_api",
                    endpoint=endpoint,
                    reason="http_404",
                    http_status=404,
                )

            if resp.status_code in _RETRY_STATUS and attempt < attempts:
                delay = min(0.4 * (2 ** (attempt - 1)), 2.0) + random.uniform(0, 0.2)
                logger.warning(
                    "API %s returned %s — retry %d/%d in %.2fs",
                    url_path, resp.status_code, attempt, attempts, delay,
                )
                time.sleep(delay)
                last_reason = f"http_{resp.status_code}"
                continue

            if resp.status_code != 200:
                return Retrieved(
                    Outcome.UNAVAILABLE,
                    tier="live_api",
                    endpoint=endpoint,
                    reason=f"http_{resp.status_code}",
                    http_status=resp.status_code,
                    detail=resp.text[:300],
                )

            try:
                raw = resp.json()
            except Exception:
                body = resp.text
                if not body or not body.strip():
                    return Retrieved(
                        Outcome.EMPTY,
                        tier="live_api",
                        endpoint=endpoint,
                        reason="empty_body",
                        http_status=200,
                    )
                return Retrieved(
                    Outcome.INVALID,
                    tier="live_api",
                    endpoint=endpoint,
                    reason="non_json_body",
                    http_status=200,
                    detail=body[:300],
                )

            payload, envelope_note = extract_data_safe(raw)
            res = classify_payload(payload, tier="live_api", endpoint=endpoint)
            res.http_status = 200
            if envelope_note:
                res.detail = envelope_note
            return res

        except httpx.TimeoutException:
            last_reason, last_detail = "timeout", f"exceeded {timeout}s"
            if attempt < attempts:
                time.sleep(0.3 * attempt)
                continue
            return Retrieved(
                Outcome.UNAVAILABLE,
                tier="live_api",
                endpoint=endpoint,
                reason="timeout",
                detail=last_detail,
            )
        except Exception as e:
            last_reason, last_detail = "transport_error", str(e)[:300]
            if attempt < attempts:
                time.sleep(0.3 * attempt)
                continue
            return Retrieved(
                Outcome.UNAVAILABLE,
                tier="live_api",
                endpoint=endpoint,
                reason="transport_error",
                detail=last_detail,
            )

    return Retrieved(
        Outcome.UNAVAILABLE,
        tier="live_api",
        endpoint=endpoint,
        reason=last_reason,
        detail=last_detail,
        http_status=last_status,
    )


async def call_api_async(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    base_url: str = "",
    auth_token: str = "",
    timeout: float = 6.0,
) -> Tuple[bool, Any]:
    """Execute an asynchronous GET call against the Accutax backend REST API."""
    client = get_async_client(base_url, auth_token)
    url_path = _format_url_path(endpoint, path_params)
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    headers = {}
    token = auth_token or active_auth_token.get() or get_service_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.debug("API GET %s params=%s", url_path, clean_params)
    try:
        resp = await client.get(url_path, params=clean_params, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            try:
                return True, resp.json()
            except Exception:
                return True, resp.text
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except httpx.TimeoutException:
        logger.warning("API call timed out (%.1fs) for %s", timeout, url_path)
        return False, f"API call timed out after {timeout}s"
    except Exception as e:
        logger.warning("API call failed for %s: %s", url_path, e)
        return False, str(e)


def call_api(
    endpoint: str,
    path_params: Dict[str, Any],
    query_params: Dict[str, Any],
    base_url: str = "",
    auth_token: str = "",
    timeout: float = 6.0,
) -> Tuple[bool, Any]:
    """Execute a synchronous GET call against the Accutax backend REST API."""
    client = get_sync_client(base_url, auth_token)
    url_path = _format_url_path(endpoint, path_params)
    clean_params = {k: v for k, v in query_params.items() if v is not None}

    headers = {}
    token = auth_token or active_auth_token.get() or get_service_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.debug("API GET %s params=%s", url_path, clean_params)
    try:
        resp = client.get(url_path, params=clean_params, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            try:
                return True, resp.json()
            except Exception:
                return True, resp.text
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except httpx.TimeoutException:
        logger.warning("API call timed out (%.1fs) for %s", timeout, url_path)
        return False, f"API call timed out after {timeout}s"
    except Exception as e:
        logger.warning("API call failed for %s: %s", url_path, e)
        return False, str(e)

