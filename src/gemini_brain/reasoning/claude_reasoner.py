"""
claude_reasoner.py — Anthropic Claude reasoning over live REST API data.

Extracted from gemini_brain_adapter.py lines 122-134 (_ANALYST_SYSTEM)
and lines 329-390 (_reason).
Formats live API data, injects project knowledge base documents (if session provided),
and executes reasoning call using BedrockAdapter or custom model adapter resolver.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from gemini_brain.config.constants import (
    COMPLEXITY_MODEL_MAP,
    HAIKU45_ID,
    REASONING_MAX_CHARS,
    REASONING_MAX_ITEMS,
)
from gemini_brain.reasoning.bedrock_client import BedrockAdapter

logger = logging.getLogger("gemini_brain.reasoning.claude_reasoner")

# ── Verbatim Analyst System Prompt ───────────────────────────────────────────
ANALYST_SYSTEM_PROMPT: str = """You are an expert financial analyst for Al Noor Technologies LLC, a UAE company.
- Currency: AED (Arab Emirates Dirham). Format as: AED X,XXX.XX
- VAT: 5%. Today: {today}. System: Accutax cloud accounting.

You have been given LIVE data retrieved from the accounting system REST API — this is the source of truth.

Answer the user's question clearly and accurately:
- For lists: show key fields (number, date, contact, amount, status)
- For reports: highlight the key metrics and what they mean
- For analysis: provide insights, not just data dumps
- For forecasts: be explicit about assumptions
- Do NOT mention API endpoints, JSON keys, or technical implementation details
- Start your answer directly — no preamble"""


def reason_over_data(
    query: str,
    data: Any,
    endpoint: str,
    complexity: str,
    session_id: Optional[str] = None,
    selected_model_key: Optional[str] = None,
    adapter_resolver: Optional[Callable[[str], Any]] = None,
    get_project_context_by_session: Optional[Callable[[str], Optional[Dict]]] = None,
) -> Tuple[str, str, int, int]:
    """Execute reasoning call over API data using Claude on AWS Bedrock.

    Parameters
    ----------
    query : str
        User's question.
    data : Any
        Live data retrieved from REST API call.
    endpoint : str
        Endpoint path that returned the data.
    complexity : str
        Complexity tier: "SIMPLE", "MEDIUM", or "COMPLEX".
    session_id : str, optional
        Session ID for project context retrieval.
    selected_model_key : str, optional
        Optional model key override for model arena comparison.
    adapter_resolver : callable, optional
        Optional function ``(model_key) -> adapter`` to decouple model arena imports.
    get_project_context_by_session : callable, optional
        Function to retrieve project context dict for a session.

    Returns
    -------
    Tuple[str, str, int, int]
        (answer_text, model_label, input_tokens, output_tokens)
    """
    if selected_model_key and selected_model_key != "gemini_brain" and adapter_resolver is not None:
        adapter = adapter_resolver(selected_model_key)
        label = getattr(adapter, "label", selected_model_key)
    else:
        model_id, label = COMPLEXITY_MODEL_MAP.get(
            complexity, (HAIKU45_ID, "Claude Haiku 4.5")
        )
        adapter = BedrockAdapter(model_id=model_id, label=label)

    logger.info("GeminiBrain → %s (complexity=%s)", label, complexity)

    # Compact data for prompt (max 40 items if list/dict with items)
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        display = {**data, "items": data["items"][:REASONING_MAX_ITEMS]}
    elif isinstance(data, list):
        display = data[:REASONING_MAX_ITEMS]
    else:
        display = data

    data_str = json.dumps(display, default=str, ensure_ascii=False)
    if len(data_str) > REASONING_MAX_CHARS:
        data_str = data_str[:REASONING_MAX_CHARS] + "\n... (truncated)"

    system = ANALYST_SYSTEM_PROMPT.format(today=datetime.date.today().isoformat())

    # Project context enrichment if session_id provided
    if session_id and get_project_context_by_session is not None:
        project_context = get_project_context_by_session(session_id)
        if project_context:
            if project_context.get("files"):
                system += "\n\nProject Knowledge Base Documents:\n"
                for f in project_context["files"]:
                    system += f"--- Document: {f['filename']} ---\n{f['content']}\n"
            if project_context.get("cross_chat_history"):
                system += "\n\nContext & Data learned from other chats in this same project:\n"
                for chat in project_context["cross_chat_history"]:
                    system += f"--- Thread: {chat['name']} ---\n"
                    for msg in chat["messages"]:
                        system += f"{msg['role'].capitalize()}: {msg['content']}\n"

    user_msg = (
        f"User question: {query}\n\n"
        f"Live data from `{endpoint}`:\n```json\n{data_str}\n```\n\n"
        f"Please answer the question based on this live data."
    )

    answer = adapter.converse(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        temperature=0.0,
        max_tokens=1500,
    )
    tu = adapter.get_token_usage()
    return (
        answer.strip(),
        label,
        tu.get("input_tokens", 0),
        tu.get("output_tokens", 0),
    )
