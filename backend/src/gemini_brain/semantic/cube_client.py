"""
cube_client.py — The only path from Gemini Brain to Cube Core.

Callers pass organization IDs that api.auth.authorize_org_scope already
approved. This module binds them into a short-lived token, runs one query,
and checks that every returned row belongs to one of them.

Cube answers a slow query with HTTP 200 and {"error": "Continue wait"}. That
means "ask again", never "no data", so load() polls until it has a result or
the request deadline passes.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Set

import httpx
import jwt

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.semantic.cube_client")
_audit = logging.getLogger("gemini_brain.audit.org_access")

#: Cube's queryRewrite refuses tokens that live longer than 120 s.
TOKEN_TTL_SECONDS = 60
_RETRY_PAUSE_SECONDS = 0.25
#: One HTTP call may wait this long. Cube holds a slow request before answering
#: "Continue wait", and its own query timeout is 20 s; the request deadline still bounds every call.
_PER_CALL_TIMEOUT_SECONDS = 30.0
_MIN_SECRET_LENGTH = 32


class CubeError(RuntimeError):
    """Cube gave no usable answer. Never turn this into an empty result."""


class CubeScopeViolation(CubeError):
    """Cube returned a row for an organization the token did not name."""


@dataclass(frozen=True)
class CubeResult:
    rows: List[Dict[str, Any]]
    query: Dict[str, Any]
    elapsed_ms: int
    last_refresh_time: Optional[str] = None
    used_pre_aggregations: List[str] = field(default_factory=list)


_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def _http() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                base_url=settings.cube_api_url,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _client


def set_http_client(client: Optional[httpx.Client]) -> None:
    """Tests inject an httpx.Client backed by httpx.MockTransport."""
    global _client
    with _client_lock:
        _client = client


def _token(organization_ids: Sequence[int], subject: str) -> str:
    secret = settings.cube_api_secret or ""
    if len(secret) < _MIN_SECRET_LENGTH:
        raise CubeError("CUBE_API_SECRET is not configured")
    orgs = sorted({int(o) for o in organization_ids})
    if not orgs:
        raise CubeError("No organization scope")
    now = int(time.time())
    return jwt.encode(
        {
            "organization_ids": orgs,
            "sub": subject,
            "iss": settings.cube_jwt_issuer,
            "aud": settings.cube_jwt_audience,
            "iat": now,
            "exp": now + TOKEN_TTL_SECONDS,
            "jti": uuid.uuid4().hex,
        },
        secret,
        algorithm="HS256",
    )


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise CubeError("Cube query exceeded the request deadline")
    return left


def _json(resp: httpx.Response) -> Dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as e:
        raise CubeError(f"Cube returned non-JSON (HTTP {resp.status_code})") from e
    if not isinstance(body, dict):
        raise CubeError("Cube returned an unexpected payload")
    return body


def view_of(query: Dict[str, Any]) -> str:
    """The single view a query uses. A query spanning views is refused."""
    names = list(query.get("measures") or []) + list(query.get("dimensions") or [])
    views = {str(n).split(".", 1)[0] for n in names}
    if len(views) != 1:
        raise CubeError(f"A query must use exactly one view, got {sorted(views)}")
    return views.pop()


def load(
    query: Dict[str, Any],
    *,
    organization_ids: Sequence[int],
    subject: str,
    deadline: float,
) -> CubeResult:
    """Run one semantic query for already-authorized organizations.

    `deadline` is a time.monotonic() value. The query is always grouped by
    organization_id, so every row can be checked against the token.
    """
    allowed: Set[int] = {int(o) for o in organization_ids}
    if not allowed:
        raise CubeError("No organization scope")
    query = dict(query)
    org_key = f"{view_of(query)}.organization_id"
    dims = list(query.get("dimensions") or [])
    if org_key not in dims:
        dims.insert(0, org_key)
    query["dimensions"] = dims

    # The token must outlive the polling loop.
    deadline = min(deadline, time.monotonic() + TOKEN_TTL_SECONDS - 5)
    token = _token(allowed, subject)
    started = time.monotonic()
    while True:
        try:
            resp = _http().post(
                "/cubejs-api/v1/load",
                json={"query": query},
                headers={"Authorization": token},
                timeout=min(_remaining(deadline), _PER_CALL_TIMEOUT_SECONDS),
            )
        except httpx.HTTPError as e:
            raise CubeError(f"Cube unreachable: {e}") from e
        body = _json(resp)
        if resp.status_code == 200 and body.get("error") == "Continue wait":
            time.sleep(min(_RETRY_PAUSE_SECONDS, _remaining(deadline)))
            continue
        if resp.status_code != 200 or "error" in body:
            raise CubeError(
                f"Cube refused the query (HTTP {resp.status_code}): {str(body.get('error'))[:300]}"
            )
        break

    rows = body.get("data")
    if not isinstance(rows, list):
        raise CubeError("Cube response has no data array")
    _check_scope(rows, org_key, allowed, query)

    elapsed = int((time.monotonic() - started) * 1000)
    used = body.get("usedPreAggregations")
    result = CubeResult(
        rows=_typed(rows, body.get("annotation") or {}),
        query=query,
        elapsed_ms=elapsed,
        last_refresh_time=body.get("lastRefreshTime"),
        used_pre_aggregations=sorted(used) if isinstance(used, dict) else [],
    )
    logger.info(
        "cube.load view=%s rows=%d ms=%d pre_aggregations=%s",
        org_key.split(".", 1)[0], len(rows), elapsed, result.used_pre_aggregations,
    )
    _trace(org_key.split(".", 1)[0], query, len(rows), elapsed)
    return result


def meta(*, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    """The views, members and descriptions Cube exposes (raw cubes are hidden)."""
    try:
        resp = _http().get(
            "/cubejs-api/v1/meta",
            headers={"Authorization": _token(organization_ids, subject)},
            timeout=min(_remaining(deadline), _PER_CALL_TIMEOUT_SECONDS),
        )
    except httpx.HTTPError as e:
        raise CubeError(f"Cube unreachable: {e}") from e
    body = _json(resp)
    if resp.status_code != 200 or "error" in body:
        raise CubeError(f"Cube meta failed (HTTP {resp.status_code}): {str(body.get('error'))[:300]}")
    return body


def _check_scope(rows: List[Dict[str, Any]], org_key: str, allowed: Set[int], query: Dict[str, Any]) -> None:
    for row in rows:
        try:
            org: Optional[int] = int(Decimal(str(row.get(org_key))))
        except (InvalidOperation, TypeError, ValueError):
            org = None
        if org not in allowed:
            _audit.critical(
                "cube_scope_violation allowed=%s got=%r query=%s", sorted(allowed), row.get(org_key), query,
            )
            raise CubeScopeViolation("Cube returned data outside the authorized organizations")


def _typed(rows: List[Dict[str, Any]], annotation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Numbers as Decimal: Cube may send them as strings, and money must not pass through float."""
    numeric = {
        name
        for section in ("measures", "dimensions")
        for name, info in (annotation.get(section) or {}).items()
        if isinstance(info, dict) and info.get("type") == "number"
    }
    typed = []
    for row in rows:
        out = dict(row)
        for name in numeric.intersection(out):
            if out[name] is not None:
                try:
                    out[name] = Decimal(str(out[name]))
                except InvalidOperation as e:
                    raise CubeError(f"Non-numeric {name}: {out[name]!r}") from e
        typed.append(out)
    return typed


def _trace(view: str, query: Dict[str, Any], row_count: int, elapsed_ms: int) -> None:
    """Show the call in the trace panel next to the REST calls. Never fails the query."""
    try:
        from gemini_brain.observability.api_tracer import record_api_trace

        record_api_trace(
            endpoint=f"cube:{view}",
            method="POST",
            query_params={"query": query},
            status_code=200,
            outcome="ok",
            duration_ms=float(elapsed_ms),
            row_count=row_count,
            source="cube",
        )
    except Exception as e:
        logger.debug("cube trace not recorded: %s", e)
