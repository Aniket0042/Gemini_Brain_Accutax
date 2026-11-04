import hashlib
import json
import threading

from cachetools import TTLCache

from gemini_brain.insights.config import get_settings
from gemini_brain.insights.schemas.response import InsightResponse

_settings = get_settings()
_cache: TTLCache = TTLCache(maxsize=_settings.cache_max_entries, ttl=_settings.cache_ttl_seconds)
_lock = threading.Lock()


def make_cache_key(organization_id: int, widget_id: str, date_range: dict | None, data: dict) -> str:
    payload = json.dumps(
        {"org": organization_id, "widget": widget_id, "range": date_range, "data": data},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_cached(key: str) -> InsightResponse | None:
    with _lock:
        return _cache.get(key)


def set_cached(key: str, response: InsightResponse) -> None:
    with _lock:
        _cache[key] = response
