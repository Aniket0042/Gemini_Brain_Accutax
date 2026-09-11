"""
intent_classifier.py — 7-type intent classification using Gemini 2.5 Flash.

Extracted from gemini_brain_adapter.py lines 56-68 (_ROUTER_SYSTEM) and lines 175-186 (_classify).
Verbatim system prompt, exact JSON schema, and fallback rules preserved.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger("gemini_brain.classification.intent_classifier")

# ── Verbatim System Prompt ───────────────────────────────────────────────────
ROUTER_SYSTEM_PROMPT: str = """You are the intent router for an AI-powered accounting assistant (Accutax).
Classify the user query into EXACTLY ONE of these 7 types:

  1  FAQ / How-to       — General usage questions ("How do I create an invoice?")
  2  App Guidance       — UI/navigation help ("Where is the expense module?")
  3  Report Generation  — Structured financial reports ("Show P&L", "Balance sheet", "Trial balance")
  4  Data Query         — Live data lookups ("Total revenue this year", "Top 5 customers")
  5  Forecast           — Future-looking predictions ("Forecast next quarter", "Expected cash flow")
  6  Accounting Concept — Definitions and theory ("What is AR?", "Explain VAT")
  7  Summary & Advice   — Strategic summaries ("Business health check", "What should I focus on?")

Questions about THIS chat ("what was my first query?", "what did I just ask?",
"summarize this conversation") are type 1 — they are not data lookups.

Respond ONLY in this JSON format (no markdown, no explanation):
{"type": <1-7>, "reason": "<one-line explanation>"}
Never answer the user's question. Classify it."""


def classify_intent(
    query: str,
    call_gemini: Callable[[str, str, int], Tuple[str, int, int]],
    parse_json: Callable[[str, Dict], Dict],
    session_state: Optional[Dict] = None,
) -> Tuple[Dict, int, int]:
    """Classify user query into 1 of 7 intent types using Gemini 2.5 Flash.

    Parameters
    ----------
    query : str
        User's natural-language query.
    call_gemini : callable
        ``(system, user_text, max_tokens) -> (text, input_tokens, output_tokens)``
    parse_json : callable
        ``(text, default_dict) -> dict``
    session_state : Optional[Dict]
        Active session conversation context.

    Returns
    -------
    Tuple[Dict, int, int]
        (result_dict, prompt_token_count, candidates_token_count)
        result_dict contains keys: "type" (int 1-7), "reason" (str).
    """
    system_prompt = ROUTER_SYSTEM_PROMPT
    if session_state:
        from gemini_brain.memory.conversation_window import append_memory_block
        context_parts = []
        if session_state.get("last_executed_task"):
            context_parts.append(f"- Previous Topic/Task: {session_state['last_executed_task']}")
        if session_state.get("active_year"):
            context_parts.append(f"- Active Year: {session_state['active_year']}")
        if session_state.get("contact_name"):
            context_parts.append(f"- Active Contact: {session_state['contact_name']}")
        if session_state.get("bank_account"):
            context_parts.append(f"- Active Bank: {session_state['bank_account']}")

        if context_parts:
            system_prompt += "\n\nACTIVE CONVERSATION CONTEXT:\n" + "\n".join(context_parts)
            system_prompt += "\nIf the user's query is a follow-up or relative question (e.g. 'what about Q2?'), classify it according to the topic under discussion."
        system_prompt = append_memory_block(system_prompt, session_state, for_routing=True)
        system_prompt += "\n\nEven when CONVERSATION SO FAR is present, reply with ONLY the JSON object."

    classify_user = (
        "Classify the following user query. Reply with only the JSON object — "
        "do not answer the query.\n\n"
        f"Query: {query}"
    )
    try:
        text, ri, ro = call_gemini(system_prompt, classify_user, 250)
        d = parse_json(text, {"type": 4, "reason": "parse_failed"})
        if (d or {}).get("reason") == "parse_failed":
            logger.warning("Intent JSON parse failed; raw=%.200s", (text or "").replace("\n", " "))
            from gemini_brain.memory.conversation_window import is_conversation_meta_query
            if is_conversation_meta_query(query):
                return {"type": 1, "reason": "conversation_meta_after_parse_fail"}, ri, ro
        t = int(d.get("type", 4))
        if t not in range(1, 8):
            t = 4
        d["type"] = t
        return d, ri, ro
    except Exception as e:
        logger.warning("Classification failed: %s", e)
        from gemini_brain.memory.conversation_window import is_conversation_meta_query
        if is_conversation_meta_query(query):
            return {"type": 1, "reason": "conversation_meta_after_error"}, 0, 0
        return {"type": 4, "reason": "error"}, 0, 0

