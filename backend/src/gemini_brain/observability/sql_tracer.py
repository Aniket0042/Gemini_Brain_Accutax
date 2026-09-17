"""
sql_tracer.py — Request-scoped SQL query tracer for Gemini Brain.

Tracks all database queries executed during a query request lifecycle,
measuring execution duration and row counts. Controlled by SHOW_SQL_TRACES.
Supports both ContextVar and thread-safe registry across Starlette SSE streaming threads.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gemini_brain.config.settings import settings

_CURRENT_SQL_TRACES: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "current_sql_traces", default=None
)

_LOCK = threading.Lock()
_ACTIVE_TRACES: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
_LATEST_TRACE_ID: Optional[str] = None


def is_sql_tracing_enabled() -> bool:
    """Return whether SQL tracing is active per configuration."""
    return bool(getattr(settings, "show_sql_traces", True))


def start_sql_trace(trace_id: Optional[str] = None) -> str:
    """Initialize a fresh trace list for the current request context."""
    global _LATEST_TRACE_ID
    tid = trace_id or str(uuid.uuid4())
    if is_sql_tracing_enabled():
        with _LOCK:
            _ACTIVE_TRACES[tid] = []
            if len(_ACTIVE_TRACES) > 200:
                _ACTIVE_TRACES.popitem(last=False)
            _LATEST_TRACE_ID = tid
        _CURRENT_SQL_TRACES.set(_ACTIVE_TRACES[tid])
    else:
        _CURRENT_SQL_TRACES.set(None)
    return tid


def record_sql_trace(
    sql: str,
    duration_ms: float = 0.0,
    row_count: int = 0,
    source: str = "database",
    trace_id: Optional[str] = None,
) -> None:
    """Record a single executed SQL query into the active trace context."""
    if not is_sql_tracing_enabled():
        return

    cleaned_sql = sql.strip() if sql else ""
    if not cleaned_sql or cleaned_sql.startswith("SET LOCAL") or cleaned_sql.startswith("SET TRANSACTION"):
        return

    entry = {
        "sql": cleaned_sql,
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
    ctx_traces = _CURRENT_SQL_TRACES.get()
    if ctx_traces is not None:
        ctx_traces.append(entry)
        return

    # 3. Fallback to latest active trace in registry across thread boundaries
    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            _ACTIVE_TRACES[_LATEST_TRACE_ID].append(entry)
            return


def get_sql_traces(trace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all SQL queries recorded in the current request context."""
    if not is_sql_tracing_enabled():
        return []

    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[trace_id])

    ctx_traces = _CURRENT_SQL_TRACES.get()
    if ctx_traces:
        return list(ctx_traces)

    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[_LATEST_TRACE_ID])

    return []


def clear_sql_trace(trace_id: Optional[str] = None) -> None:
    """Clear the active SQL trace context."""
    global _LATEST_TRACE_ID
    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            del _ACTIVE_TRACES[trace_id]
        if _LATEST_TRACE_ID == trace_id:
            _LATEST_TRACE_ID = None
    _CURRENT_SQL_TRACES.set(None)

