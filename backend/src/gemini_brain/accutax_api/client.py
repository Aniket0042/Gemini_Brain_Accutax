"""client.py: the Accutax REST API, called as the signed-in person.

Every call carries the person's own Accutax bearer (the `accutax_token` claim of the AI token), so Accutax's own
guards (JwtAuthGuard, PermissionGuard) decide what they may read or change. The assistant never writes to the
database itself: its database role is read-only.

The bearer is wrapped in AccutaxCredential, whose repr hides it, so it cannot reach a prompt, a log line or an
exception message by accident. Request and response bodies are never logged either: only method, path, status
and time.

Only GET is retried (once, on a dropped connection or a 502/503/504). A POST or PUT is never retried here: a
create that timed out may still have run, so the caller decides (see actions/pending.py, which makes each
confirm run at most once).
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import httpx

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.accutax_api")

_RETRY_STATUS = {502, 503, 504}

#: What the person is told for each kind of failure. Never Accutax's raw message for 5xx (it can hold internals).
USER_MESSAGES = {
    "auth": "Your Accutax sign-in has expired. Sign in again, then try once more.",
    "permission": "Your Accutax role does not allow this action for this company.",
    "not_found": "Accutax could not find that record.",
    "validation": "Accutax did not accept the details.",
    "conflict": "Accutax refused this because the record changed or already exists.",
    "unavailable": "Accutax is not reachable right now. Nothing was changed; try again in a minute.",
    "error": "Accutax returned an error. Nothing was confirmed as changed.",
}


@dataclass(frozen=True)
class AccutaxCredential:
    """The person's Accutax bearer. Its repr and str never show the token."""

    token: str = field(repr=False)

    def __bool__(self) -> bool:
        return bool(self.token)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "AccutaxCredential(***)"

    def organization_id(self) -> Optional[int]:
        """The organization_id claim of the token, or None when it cannot be read. Not verified here (Accutax
        verifies the token on every call). Accutax runs some actions in the organization of the token, whatever
        organization the request names, so a caller must compare this with the organization it means."""
        try:
            part = self.token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            value = claims.get("organization_id")
            return int(value) if value not in (None, "") else None
        except (IndexError, ValueError, TypeError, AttributeError):
            return None

    @classmethod
    def of(cls, token: Optional[str]) -> Optional["AccutaxCredential"]:
        token = (token or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        return cls(token) if token else None


class AccutaxApiError(Exception):
    """A failed Accutax call. `kind` is a key of USER_MESSAGES; `detail` is Accutax's own message for 4xx only."""

    def __init__(self, kind: str, status: Optional[int] = None, detail: str = ""):
        self.kind = kind if kind in USER_MESSAGES else "error"
        self.status = status
        self.detail = detail
        super().__init__(f"Accutax {self.kind}" + (f" ({status})" if status else ""))

    @property
    def user_message(self) -> str:
        base = USER_MESSAGES[self.kind]
        if self.kind == "validation" and self.detail:
            return f"{base} {self.detail}"
        return base


def _kind(status: int) -> str:
    if status == 401:
        return "auth"
    if status == 403:
        return "permission"
    if status == 404:
        return "not_found"
    if status == 409:
        return "conflict"
    if status in (400, 422):
        return "validation"
    if status in _RETRY_STATUS:
        return "unavailable"
    return "error"


def _detail(response: httpx.Response) -> str:
    """Accutax's own short message for a 4xx (NestJS: {"message": str | [str]}). Capped; never for 5xx."""
    if response.status_code >= 500:
        return ""
    try:
        body = response.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    if isinstance(message, list):
        message = "; ".join(str(m) for m in message[:5])
    return str(message or "")[:300]


class AccutaxClient:
    """One person, one organization. Use as a context manager, or call close()."""

    def __init__(self, credential: AccutaxCredential, organization_id: int, *,
                 base_url: Optional[str] = None, timeout: Optional[float] = None,
                 transport: Optional[httpx.BaseTransport] = None):
        if not credential:
            raise AccutaxApiError("auth")
        self.organization_id = int(organization_id)
        self.token_organization_id = credential.organization_id()
        self._http = httpx.Client(
            base_url=(base_url or settings.accutax_base_url).rstrip("/"),
            timeout=timeout or settings.accutax_api_timeout_seconds,
            headers={
                "Authorization": f"Bearer {credential.token}",
                "x-organization-id": str(self.organization_id),
                "Accept": "application/json",
            },
            transport=transport,
        )

    def __enter__(self) -> "AccutaxClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self._call("GET", path, params=params, retries=1)

    def post(self, path: str, body: Any = None, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self._call("POST", path, json=body, params=params)

    def put(self, path: str, body: Any = None, params: Optional[Mapping[str, Any]] = None) -> Any:
        return self._call("PUT", path, json=body, params=params)

    def _call(self, method: str, path: str, *, retries: int = 0, **kwargs: Any) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TimeoutException as e:
                logger.warning("accutax %s %s timed out after %.1fs", method, path, time.monotonic() - started)
                if attempt < retries:
                    attempt += 1
                    continue
                # A write that timed out may still have run: the caller must not report it as not done.
                raise AccutaxApiError("unavailable" if method == "GET" else "error") from e
            except httpx.HTTPError as e:
                logger.warning("accutax %s %s failed: %s", method, path, type(e).__name__)
                if attempt < retries:
                    attempt += 1
                    continue
                # Not connected at all: nothing ran. Any other failure of a write: it may have run.
                sure = method == "GET" or isinstance(e, httpx.ConnectError)
                raise AccutaxApiError("unavailable" if sure else "error") from e
            ms = int((time.monotonic() - started) * 1000)
            logger.info("accutax %s %s -> %s in %dms", method, path, response.status_code, ms)
            if response.status_code in _RETRY_STATUS and attempt < retries:
                attempt += 1
                continue
            if response.status_code >= 400:
                kind = _kind(response.status_code)
                if method != "GET" and response.status_code in (502, 504):
                    kind = "error"  # a gateway gave up waiting: the write may still have run
                raise AccutaxApiError(kind, response.status_code, _detail(response))
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text


def client_for(credential: Optional[AccutaxCredential], organization_id: int, **kwargs: Any) -> AccutaxClient:
    """A client, or AccutaxApiError("auth") when the request carries no Accutax bearer (a locally-issued token)."""
    if not credential:
        raise AccutaxApiError("auth")
    return AccutaxClient(credential, organization_id, **kwargs)


def json_of(data: Any) -> Dict[str, Any]:
    """Accutax wraps most answers as {"data": ...}; give the inner dict, or the dict itself."""
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}
