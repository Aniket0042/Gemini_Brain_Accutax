"""envelope.py — Guarantees the response shape. Nothing null that must not be null."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .errors import ErrorCode
from .messages import notice_for
from .output_guard import sanitize_answer

_EMPTY_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "llm_calls": 0,
    "cost_usd": 0.0,
    "elapsed_seconds": 0.0,
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def build_notice(
    code: "ErrorCode | str",
    *,
    subject: str = "your records",
    request_id: str = "",
    suggestions: Optional[List[str]] = None,
    shown: Any = "",
    total: Any = "",
) -> Dict[str, Any]:
    return notice_for(
        code,
        subject=subject,
        request_id=request_id or new_request_id(),
        suggestions=suggestions,
        shown=shown,
        total=total,
    )


def build_success(
    answer: str,
    *,
    results: Optional[List[Any]] = None,
    sql: Optional[str] = None,
    data_source: Optional[Dict[str, Any]] = None,
    table_markdown: Optional[str] = None,
    token_usage: Optional[Dict[str, Any]] = None,
    agent_trace: Optional[List[Dict[str, Any]]] = None,
    routing_info: Optional[Dict[str, Any]] = None,
    query_trace: Optional[Dict[str, Any]] = None,
    request_id: str = "",
) -> Dict[str, Any]:
    return normalize_envelope({
        "answer": answer,
        "results": results or [],
        "sql": sql,
        "error": None,
        "status": "ok",
        "notice": None,
        "data_source": data_source,
        "table_markdown": table_markdown,
        "token_usage": token_usage or dict(_EMPTY_USAGE),
        "agent_trace": agent_trace or [],
        "routing_info": routing_info,
        "query_trace": query_trace,
        "request_id": request_id or new_request_id(),
    })


def build_empty(
    answer: str,
    *,
    subject: str = "your records",
    data_source: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    token_usage: Optional[Dict[str, Any]] = None,
    agent_trace: Optional[List[Dict[str, Any]]] = None,
    routing_info: Optional[Dict[str, Any]] = None,
    query_trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rid = request_id or new_request_id()
    notice = notice_for("NO_ROWS", subject=subject, request_id=rid)
    return normalize_envelope({
        "answer": answer or notice["message"],
        "results": [],
        "sql": None,
        "error": None,
        "status": "empty",
        "notice": notice,
        "data_source": data_source,
        "table_markdown": None,
        "token_usage": token_usage or dict(_EMPTY_USAGE),
        "agent_trace": agent_trace or [],
        "routing_info": routing_info,
        "query_trace": query_trace,
        "request_id": rid,
    })


def build_degraded(
    code: "ErrorCode | str",
    *,
    subject: str = "your records",
    answer: Optional[str] = None,
    data_source: Optional[Dict[str, Any]] = None,
    table_markdown: Optional[str] = None,
    request_id: str = "",
    token_usage: Optional[Dict[str, Any]] = None,
    agent_trace: Optional[List[Dict[str, Any]]] = None,
    routing_info: Optional[Dict[str, Any]] = None,
    query_trace: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rid = request_id or new_request_id()
    notice = notice_for(code, subject=subject, request_id=rid)
    code_val = code.value if hasattr(code, "value") else str(code)
    return normalize_envelope({
        "answer": answer or notice["message"],
        "results": [],
        "sql": None,
        "error": code_val,
        "status": "degraded" if notice.get("retryable", False) else "failed",
        "notice": notice,
        "data_source": data_source,
        "table_markdown": table_markdown,
        "token_usage": token_usage or dict(_EMPTY_USAGE),
        "agent_trace": agent_trace or [],
        "routing_info": routing_info,
        "query_trace": query_trace,
        "request_id": rid,
    })


def normalize_envelope(result: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce any runner result into a shape QueryResponse can always validate.

    This is the last line of defence: call it immediately before QueryResponse(**result).
    It must never raise.
    """
    out = dict(result or {})

    answer = out.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        notice = out.get("notice") or {}
        answer = (
            notice.get("message")
            or "I could not produce an answer for that request."
        )

    # Last line of defence against backend/IT-support-style leaks (e.g. "contact
    # your IT/Database team", raw SQL error text) — a deterministic text check,
    # not a prompt instruction, so it can't be skipped by whichever path answered.
    #
    # Only run it when this answer actually touched real backend data (an API
    # result, a SQL query, or a rendered table). A pure LEFT-path answer (FAQ,
    # app guidance, accounting concept, or advice with no data fetched) was
    # never given any schema/query/table content to leak in the first place —
    # applying this check to it can only ever be a false positive, e.g.
    # flagging ordinary accounting prose that happens to use hyphenated terms
    # like "accrual-basis"/"cash-basis" and silently discarding a correct
    # answer in favor of the generic fallback message.
    touched_backend_data = bool(out.get("sql")) or bool(out.get("table_markdown")) \
        or bool(out.get("data_source")) or bool(out.get("results"))
    if touched_backend_data:
        answer = sanitize_answer(answer)

    # Apply markdown normalizer if available
    try:
        from gemini_brain.formatting.markdown import normalize_markdown
        out["answer"] = normalize_markdown(answer) or answer
    except Exception:
        out["answer"] = answer

    results = out.get("results")
    if not isinstance(results, list):
        results = [] if results in (None, "", {}) else [results]
    out["results"] = results

    usage = out.get("token_usage")
    if not isinstance(usage, dict):
        usage = dict(_EMPTY_USAGE)
    for k, v in _EMPTY_USAGE.items():
        usage.setdefault(k, v)
        if usage[k] is None:
            usage[k] = v
    out["token_usage"] = usage

    if not isinstance(out.get("agent_trace"), list):
        out["agent_trace"] = []
    if not isinstance(out.get("pii_redactions"), dict):
        out["pii_redactions"] = {}
    out["pii_redacted"] = bool(out.get("pii_redacted", False))
    out.setdefault("status", "failed" if out.get("error") else "ok")
    out.setdefault("notice", None)
    out.setdefault("data_source", None)
    out.setdefault("table_markdown", None)
    out.setdefault("request_id", new_request_id())
    out.setdefault("sql", None)
    out.setdefault("error", None)
    return out
