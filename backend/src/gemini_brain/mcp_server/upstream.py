"""The Accutax AI API as the MCP tools call it: the caller's bearer forwarded, errors made readable.

The MCP server never reads the database or calls a model itself. Everything goes through /api/v1, where
authorize_org_scope, the usage limits and the org-access audit log already run.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.mcp_server.upstream")


class UpstreamError(Exception):
    """A refusal or failure from the API, worded for the person in the chat. Never carries their data."""


# What the host model shows the user for each status the API refuses with. A 403/409/429 detail is the API's
# own user-safe wording, so it is passed on; anything else gets a fixed line.
_FIXED = {
    401: "Your Accutax sign-in has expired. Reconnect the Accutax connector and try again.",
    404: "Accutax could not find that.",
    502: "Accutax AI is unavailable right now. Try again in a few minutes.",
    503: "Accutax AI is unavailable right now. Try again in a few minutes.",
    504: "Accutax AI took too long to answer. Try a narrower question.",
}
_PASS_DETAIL = {400, 403, 409, 422, 429}


def _error(resp: httpx.Response) -> UpstreamError:
    code = resp.status_code
    if code in _PASS_DETAIL:
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, str) and detail.strip():
            return UpstreamError(detail.strip())
        if code == 403:
            return UpstreamError("Your Accutax account cannot access that organization.")
    return UpstreamError(_FIXED.get(code, "Accutax AI could not answer that. Try again."))


class Upstream:
    """One API client per server process. `transport` is for tests (httpx.MockTransport)."""

    def __init__(self, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=(base_url or settings.mcp_upstream_url).rstrip("/") + "/api/v1",
            transport=transport,
            timeout=httpx.Timeout(settings.mcp_answer_timeout_seconds, connect=10.0),
        )

    async def _call(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.TimeoutException:
            raise UpstreamError(_FIXED[504]) from None
        except httpx.HTTPError as e:
            logger.warning("MCP upstream %s %s failed: %s", method, path, type(e).__name__)
            raise UpstreamError(_FIXED[503]) from None
        if resp.status_code >= 400:
            # A failed /query still returns an envelope with a user-safe notice; prefer it to the fixed line.
            if path == "/query" and resp.status_code >= 500:
                try:
                    message = (resp.json().get("notice") or {}).get("message")
                except ValueError:
                    message = None
                if message:
                    raise UpstreamError(message)
            logger.info("MCP upstream %s %s -> %s", method, path, resp.status_code)
            raise _error(resp)
        return resp.json()

    async def login(self, email: str, password: str) -> Optional[dict[str, Any]]:
        """The API's own email/password login. Its token response, or None when the credentials are wrong."""
        try:
            resp = await self._client.post("/auth/login-json", json={"username": email, "password": password})
        except httpx.HTTPError as e:
            logger.warning("MCP upstream login failed: %s", type(e).__name__)
            raise UpstreamError(_FIXED[503]) from None
        if resp.status_code == 401:
            return None
        if resp.status_code >= 400:
            logger.info("MCP upstream login -> %s", resp.status_code)
            raise UpstreamError(_FIXED.get(resp.status_code, "Accutax sign-in failed. Try again."))
        return resp.json()

    async def organizations(self, token: str) -> list[dict[str, Any]]:
        body = await self._call("GET", "/tenants", token)
        return list(body.get("tenants") or [])

    async def ask(self, token: str, question: str, *, organization_id: Optional[int] = None,
                  agent: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": question}
        if organization_id is not None:
            payload["organization_id"] = organization_id
        if agent:
            payload["agent"] = agent
        return await self._call("POST", "/query", token, json=payload)

    async def aclose(self) -> None:
        await self._client.aclose()
