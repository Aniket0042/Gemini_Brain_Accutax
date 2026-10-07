"""
preview.py — Let chosen users try the agent in the chat UI.

Users listed in AGENT_PREVIEW_USERS (emails or user ids) see "Accutax Agent
(preview)" in the model picker. A chat sent with that model is answered by the
agent instead of the current path; everyone else is unaffected. It works the
same after the Accutax SSO handoff: the Accutax token carries the email.

The turn is saved to the thread like any other answer, so the thread shows it
and follow-ups have context.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.agent.preview")

MODEL_KEY = "accutax-agent"
MODEL_LABEL = "Accutax Agent (preview)"
HISTORY_MESSAGES = 6

_STEP = {
    "query_metrics": "Fetching figures",
    "list_documents": "Listing documents",
    "search_vat_kb": "Searching UAE VAT law",
    "app_guide": "Checking the Accutax guide",
}


def _allowlist() -> set:
    return {part.strip().lower() for part in (settings.agent_preview_users or "").split(",") if part.strip()}


def allowed(user: Any) -> bool:
    """Whether this user may use the preview: email (any case) or user id on the list."""
    names = _allowlist()
    if not names or user is None:
        return False
    email = str(getattr(user, "email", "") or "").strip().lower()
    return (bool(email) and email in names) or str(getattr(user, "user_id", "")) in names


def requested(model: Any) -> bool:
    return str(model or "").strip().lower() == MODEL_KEY


def catalog_entry() -> Dict[str, Any]:
    """The picker entry, in the shape of api.models.ModelInfo."""
    return {
        "key": MODEL_KEY,
        "label": MODEL_LABEL,
        "description": ("New answer engine on governed figures (Cube): ledger P&L and balance sheet, invoices, "
                        "bills, VAT, plus UAE VAT law. Preview: answers may differ from the standard assistant."),
        "provider": "bedrock",
        "supports": ["tools"],
        "efforts": [],
        "latency_class": "standard",
        "primary": True,
        "available": True,
    }


def step_label(name: str, params: Any) -> str:
    """A short status line for one tool call."""
    label = _STEP.get(name, "Working")
    if isinstance(params, dict):
        what = params.get("view") or params.get("type")
        if what:
            label += f": {str(what).replace('_', ' ')}"
    return label + "…"


def answer(question: str, organization_ids: Sequence[int], org_meta: Callable[[], Dict[int, Dict[str, Any]]], *,
           session_id: Optional[str], user_id: int, db_name: str = "",
           progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Answer with the agent and save the turn. Returns a QueryResponse-shaped dict. Never raises."""
    from gemini_brain.agent.loop import run_agent

    started = time.monotonic()
    orgs = [int(o) for o in organization_ids]
    history: List[Dict[str, Any]] = []
    if session_id:
        try:
            from gemini_brain.memory.session_memory import get_history_by_session
            history = get_history_by_session(session_id, limit=HISTORY_MESSAGES, db_name=db_name)
        except Exception as e:  # noqa: BLE001 - answer without history rather than fail
            logger.warning("agent preview: no history for %s: %s", session_id, e)
    try:
        meta = org_meta() or {}
    except Exception as e:  # noqa: BLE001 - names only label the answer
        logger.warning("agent preview: organization names unavailable: %s", e)
        meta = {}

    on_tool = (lambda name, params: progress(step_label(name, params))) if progress else None
    result = run_agent(question, orgs, meta, history=history, subject=f"agent-preview:{user_id}", progress=on_tool)
    answer_text = result.answer or "I could not produce an answer for that request. Please try again."
    if session_id:
        _save_turn(session_id, user_id, orgs, question, answer_text, db_name)

    usage = dict(result.usage or {})
    usage["elapsed_seconds"] = round(time.monotonic() - started, 2)
    status = {"ok": "ok", "deadline": "degraded"}.get(result.status, "failed")
    return {
        "answer": answer_text,
        "status": status,
        "token_usage": usage,
        "routing_info": {
            "type": 6 if result.route == "law" else 1,
            "type_label": "UAE VAT law (knowledge base)" if result.route == "law" else "Figures (governed metrics)",
            "path": f"agent_{result.route}",
            "reason": f"{MODEL_LABEL}: {len(result.tool_calls)} tool call(s)",
            "bedrock_model": settings.agent_model_id or None,
        },
        "policy": {"model": MODEL_KEY, "model_label": MODEL_LABEL, "auto": False},
        "verification": result.verification or None,
        "agent_trace": [{"step": "tool", **{k: v for k, v in call.items() if k in ("name", "input", "ok", "ms", "rows", "error")}}
                        for call in result.tool_calls],
    }


def _save_turn(session_id: str, user_id: int, orgs: List[int], question: str, text: str, db_name: str) -> None:
    try:
        from gemini_brain.memory.session_memory import ensure_session, save_message_by_session
        scope = {"organization_ids": orgs} if len(orgs) > 1 else {"organization_id": orgs[0] if orgs else None}
        if not ensure_session(session_id, user_id, db_name=db_name, **scope):
            logger.warning("agent preview: session %s refused this scope; turn not saved", session_id)
            return
        save_message_by_session(session_id, "user", question, db_name=db_name)
        save_message_by_session(session_id, "assistant", text, db_name=db_name)
    except Exception as e:  # noqa: BLE001 - the answer still reaches the user
        logger.warning("agent preview: could not save turn to %s: %s", session_id, e)
