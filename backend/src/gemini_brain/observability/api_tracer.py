"""
api_tracer.py — Request-scoped Accutax REST API call tracer for Gemini Brain.

Mirrors sql_tracer.py exactly (ContextVar + thread-safe registry fallback for
Starlette SSE streaming threads), but records live REST calls made against the
Accutax backend instead of SQL queries: endpoint, method, path/query params,
HTTP status, outcome and duration. Controlled by SHOW_API_TRACES.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gemini_brain.config.settings import settings

_CURRENT_API_TRACES: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "current_api_traces", default=None
)

_LOCK = threading.Lock()
_ACTIVE_TRACES: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
_LATEST_TRACE_ID: Optional[str] = None


def is_api_tracing_enabled() -> bool:
    """Return whether Accutax REST API call tracing is active per configuration."""
    return bool(getattr(settings, "show_api_traces", True))


def start_api_trace(trace_id: Optional[str] = None) -> str:
    """Initialize a fresh trace list for the current request context."""
    global _LATEST_TRACE_ID
    tid = trace_id or str(uuid.uuid4())
    if is_api_tracing_enabled():
        with _LOCK:
            _ACTIVE_TRACES[tid] = []
            if len(_ACTIVE_TRACES) > 200:
                _ACTIVE_TRACES.popitem(last=False)
            _LATEST_TRACE_ID = tid
        _CURRENT_API_TRACES.set(_ACTIVE_TRACES[tid])
    else:
        _CURRENT_API_TRACES.set(None)
    return tid


def record_api_trace(
    endpoint: str,
    method: str = "GET",
    path_params: Optional[Dict[str, Any]] = None,
    query_params: Optional[Dict[str, Any]] = None,
    status_code: Optional[int] = None,
    outcome: str = "",
    duration_ms: float = 0.0,
    row_count: int = 0,
    trace_id: Optional[str] = None,
    source: str = "live_api",
) -> None:
    """Record a single Accutax REST API call (or a cache hit standing in for
    one — see result_cache in gemini_brain_runner._retrieve) into the active
    trace context. `source` distinguishes a real HTTP round trip ("live_api")
    from a served-from-cache result ("cache") so the frontend can badge it
    instead of implying a network call that never happened.
    """
    if not is_api_tracing_enabled():
        return

    cleaned_endpoint = (endpoint or "").strip()
    if not cleaned_endpoint:
        return

    entry = {
        "endpoint": cleaned_endpoint,
        "method": method,
        "path_params": path_params or {},
        "query_params": query_params or {},
        "status_code": status_code,
        "outcome": outcome,
        "duration_ms": round(float(duration_ms), 2),
        "row_count": int(row_count),
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Record by explicit trace_id if provided
    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            _ACTIVE_TRACES[trace_id].append(entry)
            return

    # 2. Record by current thread's ContextVar
    ctx_traces = _CURRENT_API_TRACES.get()
    if ctx_traces is not None:
        ctx_traces.append(entry)
        return

    # 3. Fallback to latest active trace in registry across thread boundaries
    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            _ACTIVE_TRACES[_LATEST_TRACE_ID].append(entry)
            return


def get_api_traces(trace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all Accutax REST API calls recorded in the current request context."""
    if not is_api_tracing_enabled():
        return []

    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[trace_id])

    ctx_traces = _CURRENT_API_TRACES.get()
    if ctx_traces:
        return list(ctx_traces)

    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[_LATEST_TRACE_ID])

    return []


def clear_api_trace(trace_id: Optional[str] = None) -> None:
    """Clear the active API trace context."""
    global _LATEST_TRACE_ID
    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            del _ACTIVE_TRACES[trace_id]
        if _LATEST_TRACE_ID == trace_id:
            _LATEST_TRACE_ID = None
    _CURRENT_API_TRACES.set(None)
