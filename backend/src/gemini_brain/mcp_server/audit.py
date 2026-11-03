"""Who called which MCP tool, and the per-user speed limit on tool calls.

Each tool call is one JSON line in MCP_AUDIT_LOG: time, Accutax user, the host's OAuth client, tool, organization,
specialist, a SHA-256 of the arguments, outcome and duration. The arguments themselves (the question) are never
written: the hash is enough to match a line to a complaint, and the API's own logs hold the rest.

The speed limit counts tool calls per user per minute on this server process. The API still applies its own usage
limits and credits to every question; this one stops a host from looping on cheap calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.mcp_server.audit")

_BACKEND = Path(__file__).resolve().parents[3]
_write_lock = threading.Lock()


def args_hash(arguments: Dict[str, Any]) -> str:
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path() -> Optional[Path]:
    raw = (settings.mcp_audit_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND / path


def record(**fields: Any) -> None:
    """Append one line. A log that cannot be written is reported, never fatal to the tool call."""
    path = _path()
    if path is None:
        return
    line = json.dumps({"ts": round(time.time(), 3), **fields}, ensure_ascii=False, default=str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock, path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("MCP audit log not written: %s", e)


class SpeedLimit:
    """At most `per_minute()` calls per key in any 60 seconds."""

    def __init__(self, per_minute: Callable[[], int] = lambda: settings.mcp_tool_calls_per_minute,
                 clock: Callable[[], float] = time.monotonic):
        self._per_minute = per_minute
        self._clock = clock
        self._calls: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            calls = self._calls[key]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= self._per_minute():
                return False
            calls.append(now)
            return True
