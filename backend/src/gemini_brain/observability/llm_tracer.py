"""
llm_tracer.py — Request-scoped Bedrock/Claude LLM call tracer for Gemini Brain.

Mirrors sql_tracer.py / api_tracer.py exactly (ContextVar + thread-safe registry
fallback for Starlette SSE streaming threads), but records every LLM round trip
made through BedrockAdapter (or the module-level agents/ helpers in
bedrock_client.py): model, purpose (classify/select_endpoint/narrate/sql_agent/
etc.), tokens in/out and duration. Controlled by SHOW_LLM_TRACES.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from gemini_brain.config.settings import settings

_CURRENT_LLM_TRACES: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "current_llm_traces", default=None
)

_LOCK = threading.Lock()
_ACTIVE_TRACES: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
_LATEST_TRACE_ID: Optional[str] = None


def is_llm_tracing_enabled() -> bool:
    """Return whether LLM call tracing is active per configuration."""
    return bool(getattr(settings, "show_llm_traces", True))


def start_llm_trace(trace_id: Optional[str] = None) -> str:
    """Initialize a fresh trace list for the current request context."""
    global _LATEST_TRACE_ID
    tid = trace_id or str(uuid.uuid4())
    if is_llm_tracing_enabled():
        with _LOCK:
            _ACTIVE_TRACES[tid] = []
            if len(_ACTIVE_TRACES) > 200:
                _ACTIVE_TRACES.popitem(last=False)
            _LATEST_TRACE_ID = tid
        _CURRENT_LLM_TRACES.set(_ACTIVE_TRACES[tid])
    else:
        _CURRENT_LLM_TRACES.set(None)
    return tid


def record_llm_trace(
    model_id: str,
    label: str = "",
    purpose: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: float = 0.0,
    trace_id: Optional[str] = None,
) -> None:
    """Record a single Bedrock/Claude LLM call into the active trace context."""
    if not is_llm_tracing_enabled():
        return

    cleaned_model = (model_id or "").strip()
    if not cleaned_model:
        return

    entry = {
        "model_id": cleaned_model,
        "label": label or cleaned_model,
        "purpose": purpose or "unspecified",
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "duration_ms": round(float(duration_ms), 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Record by explicit trace_id if provided
    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            _ACTIVE_TRACES[trace_id].append(entry)
            return

    # 2. Record by current thread's ContextVar
    ctx_traces = _CURRENT_LLM_TRACES.get()
    if ctx_traces is not None:
        ctx_traces.append(entry)
        return

    # 3. Fallback to latest active trace in registry across thread boundaries
    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            _ACTIVE_TRACES[_LATEST_TRACE_ID].append(entry)
            return


def get_llm_traces(trace_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all LLM calls recorded in the current request context."""
    if not is_llm_tracing_enabled():
        return []

    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[trace_id])

    ctx_traces = _CURRENT_LLM_TRACES.get()
    if ctx_traces:
        return list(ctx_traces)

    with _LOCK:
        if _LATEST_TRACE_ID and _LATEST_TRACE_ID in _ACTIVE_TRACES:
            return list(_ACTIVE_TRACES[_LATEST_TRACE_ID])

    return []


def clear_llm_trace(trace_id: Optional[str] = None) -> None:
    """Clear the active LLM trace context."""
    global _LATEST_TRACE_ID
    with _LOCK:
        if trace_id and trace_id in _ACTIVE_TRACES:
            del _ACTIVE_TRACES[trace_id]
        if _LATEST_TRACE_ID == trace_id:
            _LATEST_TRACE_ID = None
    _CURRENT_LLM_TRACES.set(None)
