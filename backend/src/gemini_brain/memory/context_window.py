"""
context_window.py — Fixed-budget session context-window meter.

Tracks cumulative input+output tokens spent across every turn of a session
against a single fixed ceiling (SESSION_CONTEXT_WINDOW_TOKENS), independent of
which model answered any given turn. Mirrors the "355.7k / 1M" style meter in
Claude's own UI: that number is an account-level budget, not any one model's
raw context window, and this is the same idea sized to this app's model
catalog (see policy/registry.py — real windows range 200K-1M across models).

Storage reuses the same conversation_state JSONB column session_memory.py
already reads/writes for slot-state and rolling-summary — no new table.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from gemini_brain.config.constants import SESSION_CONTEXT_WINDOW_TOKENS
from gemini_brain.memory.session_memory import (
    get_state_by_session,
    is_valid_uuid,
    update_state_by_session,
)

logger = logging.getLogger("gemini_brain.memory.context_window")

_STATE_KEY = "context_tokens_used"


def _snapshot(used: int, limit: int = SESSION_CONTEXT_WINDOW_TOKENS) -> Dict[str, Any]:
    used = max(0, int(used))
    percent = round(min(used / limit, 1.0) * 100, 1) if limit > 0 else 0.0
    return {
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0),
        "percent": percent,
    }


def track_context_window_usage(
    session_id: Optional[str],
    input_tokens: int,
    output_tokens: int,
    db_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Add this turn's tokens to the session's running total and persist it.

    Returns the updated {used, limit, remaining, percent} snapshot, or None
    when there is no session to track against (no session_id, or an
    unauthenticated/internal call) — the caller should omit the meter in that
    case rather than show a fabricated per-request-only number.

    Read-modify-write against conversation_state, same non-atomic pattern
    already used by conversation_window.maybe_roll_summary for the same
    column — acceptable here since a lost increment under concurrent turns on
    one session just undercounts the meter slightly, never corrupts it.
    """
    if not session_id or not is_valid_uuid(session_id):
        return None

    added = max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0))
    try:
        state = dict(get_state_by_session(session_id, db_name=db_name) or {})
        prior_used = int(state.get(_STATE_KEY) or 0)
        new_used = prior_used + added
        state[_STATE_KEY] = new_used
        update_state_by_session(session_id, state, db_name=db_name)
        return _snapshot(new_used)
    except Exception as e:
        logger.warning("Context-window tracking failed for session %s: %s", session_id, e)
        return None


def get_context_window_usage(session_id: Optional[str], db_name: str = "") -> Optional[Dict[str, Any]]:
    """Read-only snapshot of a session's current usage, without adding to it."""
    if not session_id or not is_valid_uuid(session_id):
        return None
    try:
        state = get_state_by_session(session_id, db_name=db_name) or {}
        return _snapshot(int(state.get(_STATE_KEY) or 0))
    except Exception as e:
        logger.warning("Context-window read failed for session %s: %s", session_id, e)
        return None
