"""Private AI authentication. Browser cookies and product JWTs terminate at Nest."""
from __future__ import annotations

import hmac
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request

from gemini_brain.config.settings import settings


@lru_cache(maxsize=4)
def load_gateway_configuration(path: str) -> dict:
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_mode & 0o077:
            raise ValueError()
        raw = json.loads(source.read_text())
        for name in ("gatewayToAiKey", "aiToGatewayKey"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw[name]):
                raise ValueError()
        if raw["gatewayToAiKey"] == raw["aiToGatewayKey"]:
            raise ValueError()
        for name in ("upstream", "resolveUrl"):
            url = urlsplit(raw[name])
            if url.username or url.password or url.query or url.fragment:
                raise ValueError()
            local_http = url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
            if url.scheme != "https" and not local_http:
                raise ValueError()
            if os.getenv("NODE_ENV") == "production" and url.scheme != "https":
                raise ValueError()
            if name == "resolveUrl" and url.path != "/internal/ai/resolve":
                raise ValueError()
            if name == "upstream" and (url.path not in ("", "/") or raw[name].endswith("/")):
                raise ValueError()
        return raw
    except Exception as error:
        raise RuntimeError("AI gateway requires private configuration, independent service credentials and trusted endpoints") from error


class GatewayAuthority:
    def __init__(self, request: Request):
        try:
            self.config = load_gateway_configuration(settings.ai_gateway_config_file)
        except RuntimeError as error:
            raise HTTPException(503, "AI authentication configuration is unavailable") from error
        key = request.headers.get("authorization", "")
        if (request.headers.get("cookie") or request.headers.get("origin")
                or not hmac.compare_digest(key.encode(), f"Bearer {self.config['gatewayToAiKey']}".encode())):
            raise HTTPException(401, "Authenticated AI gateway required")
        self.delegation = request.headers.get("x-accutax-delegation", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", self.delegation):
            raise HTTPException(401, "AI delegation required")
        self.method, self.path = request.method, request.url.path
        self.user_id = None

    def resolve(self) -> dict:
        try:
            response = httpx.post(self.config["resolveUrl"],
                                  headers={"Authorization": f"Bearer {self.config['aiToGatewayKey']}"},
                                  json={"delegation": self.delegation, "method": self.method, "path": self.path},
                                  timeout=httpx.Timeout(8.0, connect=2.0), follow_redirects=False)
        except Exception as error:
            raise HTTPException(503, "Current AI access could not be verified") from error
        if response.status_code in (401, 403):
            raise HTTPException(response.status_code, "AI session or company permission is no longer available")
        if response.status_code != 201 and response.status_code != 200:
            raise HTTPException(503, "Current AI access could not be verified")
        try:
            result = response.json()
            uid = result["user_id"]
            if type(uid) is not int or uid < 1 or self.user_id is not None and uid != self.user_id:
                raise ValueError()
            if not isinstance(result["allowed_org_ids"], list) or any(type(o) is not int or o < 1 for o in result["allowed_org_ids"]):
                raise ValueError()
            self.user_id = uid
            return result
        except Exception as error:
            raise HTTPException(503, "Invalid AI authority response") from error
