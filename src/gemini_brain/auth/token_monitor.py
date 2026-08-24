"""
token_monitor.py — JWT authentication token expiration monitor and health checker.

Addresses Phase F safety gap:
- Decodes JWT exp claims safely without external dependencies.
- Emits proactive warnings when the token is missing, expired, or within 24 hours of expiry.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.auth.token_monitor")


@dataclass
class TokenHealth:
    valid: bool
    status: str  # "HEALTHY" | "EXPIRING_SOON" | "EXPIRED" | "MISSING" | "INVALID"
    expires_at: Optional[float] = None
    seconds_remaining: Optional[float] = None
    message: str = ""


def inspect_jwt_token(token: Optional[str] = None) -> TokenHealth:
    """Inspect a JWT token's expiration payload without requiring signature verification.

    Parameters
    ----------
    token : Optional[str]
        Raw JWT string. Defaults to settings.accutax_auth_token.

    Returns
    -------
    TokenHealth
        Dataclass containing health status, expiration timestamp, and diagnostic message.
    """
    tok = token if token is not None else settings.accutax_auth_token
    if not tok or not tok.strip():
        msg = "ACCUTAX_AUTH_TOKEN is not configured or is empty."
        logger.warning(msg)
        return TokenHealth(valid=False, status="MISSING", message=msg)

    parts = tok.strip().split(".")
    if len(parts) != 3:
        msg = "ACCUTAX_AUTH_TOKEN is not a valid 3-part JWT token format."
        logger.error(msg)
        return TokenHealth(valid=False, status="INVALID", message=msg)

    try:
        # Base64 url-safe decode payload (part 1) with padding
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
        payload_json = base64.urlsafe_b64decode(padded).decode("utf-8")
        payload = json.loads(payload_json)
    except Exception as e:
        msg = f"Failed to decode ACCUTAX_AUTH_TOKEN payload: {e}"
        logger.error(msg)
        return TokenHealth(valid=False, status="INVALID", message=msg)

    exp = payload.get("exp")
    if exp is None:
        msg = "ACCUTAX_AUTH_TOKEN payload has no 'exp' claim (non-expiring)."
        logger.info(msg)
        return TokenHealth(valid=True, status="HEALTHY", message=msg)

    now = time.time()
    seconds_remaining = exp - now

    if seconds_remaining <= 0:
        hours_ago = round(abs(seconds_remaining) / 3600.0, 1)
        msg = f"ACCUTAX_AUTH_TOKEN EXPIRED {hours_ago} hours ago (exp={exp}, now={int(now)}). REST API calls will fail with HTTP 401."
        logger.error(msg)
        return TokenHealth(
            valid=False,
            status="EXPIRED",
            expires_at=float(exp),
            seconds_remaining=seconds_remaining,
            message=msg,
        )

    hours_left = round(seconds_remaining / 3600.0, 1)
    if seconds_remaining < 86400:  # Within 24 hours
        msg = f"ACCUTAX_AUTH_TOKEN expires soon: {hours_left} hours remaining (exp={exp}). Renew token to avoid tier outage."
        logger.warning(msg)
        return TokenHealth(
            valid=True,
            status="EXPIRING_SOON",
            expires_at=float(exp),
            seconds_remaining=seconds_remaining,
            message=msg,
        )

    days_left = round(seconds_remaining / 86400.0, 1)
    msg = f"ACCUTAX_AUTH_TOKEN is healthy ({days_left} days / {hours_left} hours remaining)."
    logger.info(msg)
    return TokenHealth(
        valid=True,
        status="HEALTHY",
        expires_at=float(exp),
        seconds_remaining=seconds_remaining,
        message=msg,
    )
