"""
state_extractor.py — Hybrid heuristic + Gemini state extraction for chat sessions.

Extracted from memory.py lines 785-888 (update_conversation_state_hybrid_by_session).
Extracts active_year, bank_account, contact_name, and last_executed_task.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from gemini_brain.config.settings import settings
from gemini_brain.memory.session_memory import (
    get_state_by_session,
    update_state_by_session,
)

logger = logging.getLogger("gemini_brain.memory.state_extractor")

STATE_EXTRACTION_SYSTEM_PROMPT: str = """You are a conversation state extractor for a financial AI assistant.
Analyze the user's latest query, the previous state, and the assistant's response to extract and output an updated JSON state.

Maintain the following keys:
- "active_year": A 4-digit year string (if mentioned or implied, e.g. "this year", "last year").
- "bank_account": Name or substring of the bank account under discussion (e.g. "HDFC", "Emirates NBD").
- "contact_name": Name of the active customer/vendor contact (e.g. "Oasis Networks").
- "last_executed_task": The general topic under discussion (e.g., "bank_balances", "profit_loss", "ar_aging").

Current Inputs:
- User Query: "{query}"
- Previous State: {prev_state}
- Assistant Response: "{response}"

Return ONLY valid JSON (no markdown block, no explanation):
{{"active_year": "...", "bank_account": "...", "contact_name": "...", "last_executed_task": "..."}}"""


def update_conversation_state_hybrid_by_session(
    session_id: str,
    user_id: int,
    query: str,
    response: str,
    agent_trace: List[Any],
    api_key: str = "",
    db_name: str = "",
) -> None:
    """Update conversation state for session using heuristic + Gemini fallback."""
    prev_state = get_state_by_session(session_id, db_name)
    state = dict(prev_state)

    heuristic_updated = False

    # 1. Heuristic Extraction
    for entry in agent_trace:
        if not isinstance(entry, dict):
            continue
        params = entry.get("params", {})
        if not isinstance(params, dict):
            continue

        bank = params.get("bank_account") or params.get("bank_name")
        if bank:
            state["bank_account"] = bank
            heuristic_updated = True

        contact = (
            params.get("customer") or params.get("vendor") or params.get("contact")
        )
        if contact:
            state["contact_name"] = contact
            heuristic_updated = True

        filters = params.get("filters", {})
        if isinstance(filters, dict):
            year = filters.get("year")
            if year:
                state["active_year"] = str(year)
                heuristic_updated = True
            c = filters.get("customer") or filters.get("vendor")
            if c:
                state["contact_name"] = c
                heuristic_updated = True

        task = entry.get("task")
        if task and task != "execute_sql":
            state["last_executed_task"] = task
            heuristic_updated = True

    # 2. Semantic Fallback via Gemini-2.5-Flash
    key = api_key or settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if not heuristic_updated and key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=key)
            prompt = STATE_EXTRACTION_SYSTEM_PROMPT.format(
                query=query,
                prev_state=json.dumps(prev_state),
                response=response,
            )

            response_obj = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=query,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    temperature=0.0,
                    max_output_tokens=150,
                ),
            )
            text = response_obj.text or ""
            raw = text.strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else parts[0]
                if raw.startswith("json"):
                    raw = raw[4:]

            extracted = json.loads(raw.strip())
            for k in ["active_year", "bank_account", "contact_name", "last_executed_task"]:
                val = extracted.get(k)
                if val and val not in ("...", "null", "None"):
                    state[k] = val
        except Exception as e:
            logger.warning(
                "Gemini state extraction failed for session %s: %s", session_id, e
            )

    update_state_by_session(session_id, state, db_name)
