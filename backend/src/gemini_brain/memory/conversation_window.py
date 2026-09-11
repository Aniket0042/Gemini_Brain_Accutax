"""
conversation_window.py — Sliding-window + rolling-summary working memory.

Loads a compact CONVERSATION SO FAR block for prompt injection, and (after a
turn) optionally compact older messages into conversation_state.summary so
prompts stay bounded.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from gemini_brain.config.constants import (
    MEMORY_ASSISTANT_TRUNCATE_CHARS,
    MEMORY_PROMPT_BUDGET_CHARS,
    MEMORY_SUMMARY_TRIGGER_MESSAGES,
    MEMORY_WINDOW_MESSAGES,
)
from gemini_brain.memory.session_memory import (
    count_messages_by_session,
    ensure_session,
    get_first_user_message_by_session,
    get_history_by_session,
    get_state_by_session,
    maybe_auto_title,
    save_message_by_session,
    update_state_by_session,
)
from gemini_brain.memory.state_extractor import update_conversation_state_hybrid_by_session

logger = logging.getLogger("gemini_brain.memory.conversation_window")

# Questions about this thread itself — must never be routed to API/SQL.
CONVERSATION_META_RE = re.compile(
    r"(?ix)"
    r"("
    r"what\s+(was|is|were)\s+(my|the)\s+(first|last|previous|earlier|original|initial)\s+(query|question|message|prompt)"
    r"|what\s+did\s+i\s+(just\s+)?(ask|say)"
    r"|what\s+(was|is)\s+(the\s+)?(first|last|previous)\s+thing\s+i\s+(asked|said)"
    r"|(repeat|remind\s+me\s+of)\s+(my|the)\s+(first\s+|last\s+|previous\s+)?(question|query|message)"
    r"|what\s+were\s+we\s+(just\s+)?(talking|discussing)\s+about"
    r"|summar(?:y|ise|ize)\s+(this|our|the)\s+(chat|conversation|thread)"
    r"|what\s+(was|is)\s+my\s+(previous|last)\s+(query|question)"
    r")"
)


def is_conversation_meta_query(query: str) -> bool:
    """True when the user is asking about this chat, not about accounting data."""
    return bool(CONVERSATION_META_RE.search((query or "").strip()))


SUMMARY_SYSTEM_PROMPT = (
    "You compact an accounting chat for later turns. Write 4-8 short bullet points "
    "covering: what the user asked, figures or reports already retrieved, the period "
    "and entities under discussion, and any unresolved follow-up. No markdown fences. "
    "Do not invent numbers that were not in the transcript."
)


def _truncate_assistant(content: str) -> str:
    text = (content or "").strip()
    if len(text) <= MEMORY_ASSISTANT_TRUNCATE_CHARS:
        return text
    return text[:MEMORY_ASSISTANT_TRUNCATE_CHARS].rstrip() + "…"


def format_memory_block(
    summary: str,
    messages: List[Dict[str, Any]],
    first_user_query: str = "",
) -> str:
    """Build the compact system-prompt block. Empty string when there is nothing to add."""
    lines: List[str] = []
    first = (first_user_query or "").strip()
    if first:
        lines.append("This thread's first user message: " + first)
    summary_text = (summary or "").strip()
    if summary_text:
        lines.append("Summary: " + summary_text)
    for msg in messages:
        role = (msg.get("role") or "user").strip().lower()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "assistant":
            content = _truncate_assistant(content)
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {content}")
    if not lines:
        return ""
    block = "CONVERSATION SO FAR:\n" + "\n".join(lines)
    if len(block) > MEMORY_PROMPT_BUDGET_CHARS:
        block = block[:MEMORY_PROMPT_BUDGET_CHARS].rstrip() + "\n…"
    return block


def append_memory_block(
    system_prompt: str,
    session_state: Optional[Dict[str, Any]],
    *,
    for_routing: bool = False,
) -> str:
    """Append the compact conversation block to a system prompt when present."""
    block = (session_state or {}).get("_memory_block") or ""
    if not block:
        return system_prompt
    if for_routing:
        hint = (
            "If the latest question is a follow-up, resolve pronouns and relative "
            "periods from CONVERSATION SO FAR, then still return only the required "
            "structured output — never answer the user in prose."
        )
    else:
        hint = (
            "You have this thread's prior turns. If the latest question is a "
            "follow-up, resolve pronouns and relative periods from CONVERSATION SO FAR. "
            "If the user asks what they asked before, quote it from that block. "
            "Never say you lack conversation history when this block is present. "
            "CONVERSATION SO FAR is background only — never continue it as a script "
            "and never write a User: or Assistant: line in your answer."
        )
    return (system_prompt or "") + "\n\n" + block + "\n" + hint


def llm_messages_from_memory(
    session_state: Optional[Dict[str, Any]],
    current_query: str,
) -> List[Dict[str, Any]]:
    """Build a Bedrock-style messages array so prior turns are real chat, not system text.

    Claude treats a single current user message as a brand-new conversation even
    when the same history is pasted into the system prompt.
    """
    raw = list((session_state or {}).get("_memory_messages") or [])
    messages: List[Dict[str, Any]] = []
    for msg in raw:
        role = (msg.get("role") or "").strip().lower()
        text = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        if role == "assistant":
            text = _truncate_assistant(text)
        if messages and messages[-1]["role"] == role:
            prev = messages[-1]["content"][0]["text"]
            messages[-1]["content"][0]["text"] = prev + "\n" + text
        else:
            messages.append({"role": role, "content": [{"text": text}]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    current = (current_query or "").strip()
    if current:
        if messages and messages[-1]["role"] == "user":
            messages.append({"role": "assistant", "content": [{"text": "(no reply recorded)"}]})
        messages.append({"role": "user", "content": [{"text": current}]})
    elif not messages:
        messages.append({"role": "user", "content": [{"text": current_query or ""}]})
    return messages


def attach_working_memory(
    session_id: Optional[str],
    user_id: int,
    organization_id: Optional[int],
    session_state: Optional[Dict[str, Any]] = None,
    db_name: str = "",
) -> Dict[str, Any]:
    """Ensure the session exists and attach `_memory_block` onto session_state."""
    state = dict(session_state or {})
    if not session_id:
        return state
    ok = ensure_session(session_id, user_id, organization_id, db_name=db_name)
    if not ok:
        return state
    persisted = get_state_by_session(session_id, db_name=db_name)
    for key, value in persisted.items():
        if key not in state or state.get(key) in (None, ""):
            state[key] = value
    history = get_history_by_session(session_id, limit=MEMORY_WINDOW_MESSAGES, db_name=db_name)
    first_user = get_first_user_message_by_session(session_id, db_name=db_name)
    state["_first_user_query"] = first_user
    state["_memory_messages"] = history
    state["_memory_block"] = format_memory_block(
        str(state.get("summary") or ""),
        history,
        first_user_query=first_user,
    )
    logger.info(
        "Working memory session=%s history=%d first_query=%s",
        session_id[:8],
        len(history),
        bool(first_user),
    )
    return state


def persist_turn(
    session_id: Optional[str],
    user_id: int,
    organization_id: Optional[int],
    user_query: str,
    assistant_answer: str,
    agent_trace: Optional[List[Any]] = None,
    call_llm: Optional[Callable[[str, str, int], Tuple[str, int, int]]] = None,
    db_name: str = "",
) -> None:
    """Write both turns, update slot state, title, and maybe roll a summary."""
    if not session_id:
        return
    if not ensure_session(session_id, user_id, organization_id, db_name=db_name):
        return
    save_message_by_session(session_id, "user", user_query, db_name=db_name)
    save_message_by_session(session_id, "assistant", assistant_answer, db_name=db_name)
    try:
        update_conversation_state_hybrid_by_session(
            session_id=session_id,
            user_id=user_id,
            query=user_query,
            response=assistant_answer,
            agent_trace=agent_trace or [],
            db_name=db_name,
        )
    except Exception as e:
        logger.warning("Slot-state update failed for session %s: %s", session_id, e)
    try:
        maybe_auto_title(session_id, user_query, call_llm, db_name=db_name)
    except Exception as e:
        logger.warning("Auto-title failed for session %s: %s", session_id, e)


def maybe_roll_summary(
    session_id: str,
    call_llm: Callable[[str, str, int], Tuple[str, int, int]],
    db_name: str = "",
) -> None:
    """If the thread is long, compact older turns into conversation_state.summary."""
    try:
        total = count_messages_by_session(session_id, db_name=db_name)
        if total < MEMORY_SUMMARY_TRIGGER_MESSAGES:
            return
        # Fetch more than the window so the model sees what is about to fall off.
        history = get_history_by_session(
            session_id,
            limit=max(total, MEMORY_SUMMARY_TRIGGER_MESSAGES),
            db_name=db_name,
        )
        older = history[:-MEMORY_WINDOW_MESSAGES] if len(history) > MEMORY_WINDOW_MESSAGES else history[:-4]
        if not older:
            return
        transcript = "\n".join(
            f"{(m.get('role') or 'user').capitalize()}: {(m.get('content') or '')[:800]}"
            for m in older
        )
        prev = get_state_by_session(session_id, db_name=db_name)
        prev_summary = str(prev.get("summary") or "")
        user_msg = (
            f"Previous summary:\n{prev_summary or '(none)'}\n\n"
            f"Older turns to fold in:\n{transcript[:6000]}"
        )
        text, _, _ = call_llm(SUMMARY_SYSTEM_PROMPT, user_msg, 400)
        summary = (text or "").strip()
        if not summary:
            return
        merged = dict(prev)
        merged["summary"] = summary
        update_state_by_session(session_id, merged, db_name=db_name)
    except Exception as e:
        logger.warning("Rolling summary failed for session %s: %s", session_id, e)


def persist_turn_and_maybe_summarize(
    session_id: Optional[str],
    user_id: int,
    organization_id: Optional[int],
    user_query: str,
    assistant_answer: str,
    agent_trace: Optional[List[Any]] = None,
    call_llm: Optional[Callable[[str, str, int], Tuple[str, int, int]]] = None,
    db_name: str = "",
) -> None:
    persist_turn(
        session_id,
        user_id,
        organization_id,
        user_query,
        assistant_answer,
        agent_trace=agent_trace,
        call_llm=call_llm,
        db_name=db_name,
    )
    if session_id and call_llm is not None:
        threading.Thread(
            target=maybe_roll_summary,
            args=(session_id, call_llm, db_name),
            daemon=True,
            name=f"chat-summary-{session_id[:8]}",
        ).start()
