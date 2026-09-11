"""
result_cache.py — In-memory & Redis-ready TTL Result Cache for tool execution results.

Phase 3 cache key structure:
  f"{org_id}:{tool}:{sha256(sorted_params_json)}:{data_version}"
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

from gemini_brain.cache.versions import get_data_version

logger = logging.getLogger("gemini_brain.cache.result_cache")


def make_cache_key(org_id: int, tool_name: str, params: Any) -> str:
    """Construct deterministic cache key for tool call."""
    if hasattr(params, "model_dump_json"):
        params_json = params.model_dump_json()
    elif isinstance(params, dict):
        params_json = json.dumps(params, sort_keys=True)
    else:
        params_json = json.dumps(str(params), sort_keys=True)

    params_hash = hashlib.sha256(params_json.encode("utf-8")).hexdigest()[:16]
    version = get_data_version(org_id)
    return f"{org_id}:{tool_name}:{params_hash}:{version}"


class ResultCache:
    """Thread-safe in-memory result cache with TTL expiry."""

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Asynchronously get item from cache if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, val = entry
            if time.time() > expires_at:
                del self._cache[key]
                return None
            logger.debug("ResultCache HIT for key=%s", key)
            return val

    def get_sync(self, key: str) -> Optional[Any]:
        """Synchronous get for sync execution paths."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, val = entry
            if time.time() > expires_at:
                del self._cache[key]
                return None
            logger.debug("ResultCache HIT for key=%s", key)
            return val

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """Asynchronously store item in cache with TTL."""
        with self._lock:
            self._cache[key] = (time.time() + ttl, value)
            logger.debug("ResultCache SET key=%s ttl=%ds", key, ttl)

    def set_sync(self, key: str, value: Any, ttl: int = 300) -> None:
        """Synchronous set for sync execution paths."""
        with self._lock:
            self._cache[key] = (time.time() + ttl, value)
            logger.debug("ResultCache SET key=%s ttl=%ds", key, ttl)

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._cache.clear()


# Module-level singleton instance
result_cache = ResultCache()
