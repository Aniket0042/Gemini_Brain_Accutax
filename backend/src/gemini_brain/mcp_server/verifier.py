"""Bearer check for the MCP endpoint: the same tokens the Accutax AI API accepts.

Phase 1 has no OAuth server yet, so a client pastes an Accutax AI (or Accutax app) bearer. Phase 2 puts an
OAuth flow in front that mints the same token, and this verifier stays as it is.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from mcp.server.auth.provider import AccessToken

from gemini_brain.api.auth import decode_access_token

logger = logging.getLogger("gemini_brain.mcp_server.verifier")

SCOPE = "accutax.read"


class AccutaxTokenVerifier:
    """A TokenVerifier (mcp.server.auth.provider) over decode_access_token.

    Only the signature, expiry and user id are checked here. Which organizations the user may read is decided
    by the API on every call, live, so a revoked grant ends there and not at token expiry.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = decode_access_token(token)
        except HTTPException:
            return None
        user = claims.get("sub") or claims.get("userId")
        if user is None or not str(user).strip().isdigit():
            logger.info("MCP bearer refused: the token names no user")
            return None
        exp = claims.get("exp")
        return AccessToken(
            token=token,
            client_id="accutax",
            scopes=[SCOPE],
            expires_at=int(exp) if isinstance(exp, (int, float)) else None,
            subject=str(user),
        )
