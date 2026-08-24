"""
claude_reasoner.py — Anthropic Claude reasoning over live REST API data.

Phase 1 optimization:
- Concise analyst system prompt (Appendix B) capping narration length.
- Hard 2000-token payload capping with row truncation notice.
- Max tokens set to 400 for low TTFT and low total duration.
- Converse streaming support for live token emission.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Callable, Dict, Generator, Optional, Tuple

from gemini_brain.config.constants import (
    COMPLEXITY_MODEL_MAP,
    HAIKU45_ID,
    NEVER_EXPOSE_BACKEND_RULE,
    REASONING_MAX_CHARS,
    REASONING_MAX_ITEMS,
)
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.reasoning.model_selector import pick_model

logger = logging.getLogger("gemini_brain.reasoning.claude_reasoner")

# ── Narration System Prompt (Appendix B) ──────────────────────────────────────
ANALYST_SYSTEM_PROMPT: str = """You are a financial analyst for Accutax, reporting to a business owner in the UAE.
Currency is AED. VAT is 5%.

The DATA block is authoritative and already fully aggregated by the system.

- Never recompute, re-sum, re-average, or re-derive any figure. Quote the numbers
  exactly as given.
- If a figure the user asked for is not present in DATA, say it is not available.
  Never estimate or infer it.
- DATA is only "truncated" if it literally contains the marker
  "[payload truncated]". If that marker is absent, do not describe the data as
  truncated, incomplete, or missing — a zero, an empty list, and an all-zero
  breakdown are complete, valid answers, not a data problem. State them plainly.
- Never mention DATA's JSON structure, field names, or shape (e.g. don't write
  "the vendors array" or "the totals section") — you are talking to a business
  owner, not describing a schema. Translate every field into plain business
  language.
- A table of this data is already displayed above your response. Do not reproduce it.
- Open with the direct answer in one sentence.
- Then at most three short bullets: what stands out, what changed, what to watch.
- Format amounts as AED 1,234,567.00.
- Maximum 120 words.
""" + NEVER_EXPOSE_BACKEND_RULE


def _format_payload_and_system(
    query: str,
    data: Any,
    endpoint: str,
    session_id: Optional[str] = None,
    get_project_context_by_session: Optional[Callable[[str], Optional[Dict]]] = None,
) -> Tuple[str, str]:
    """Format and cap data payload to 2000 tokens with truncation notice if needed."""
    total_rows = 0
    shown_rows = 0
    is_list_payload = False

    # Check and slice items
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        total_rows = len(data["items"])
        shown_rows = min(total_rows, REASONING_MAX_ITEMS)
        display = {**data, "items": data["items"][:shown_rows]}
        is_list_payload = True
    elif isinstance(data, list):
        total_rows = len(data)
        shown_rows = min(total_rows, REASONING_MAX_ITEMS)
        display = data[:shown_rows]
        is_list_payload = True
    else:
        display = data

    data_str = json.dumps(display, default=str, ensure_ascii=False)

    # 2000 tokens ≈ 8000 characters
    max_payload_chars = 8000
    if len(data_str) > max_payload_chars or (is_list_payload and shown_rows < total_rows):
        if len(data_str) > max_payload_chars:
            data_str = data_str[:max_payload_chars]
        if is_list_payload and shown_rows < total_rows:
            trunc_note = f"\n[payload truncated — {shown_rows} of {total_rows} rows shown]"
        else:
            trunc_note = "\n[payload truncated]"
        data_str += trunc_note

    system = ANALYST_SYSTEM_PROMPT

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
        f"Live DATA from `{endpoint}`:\n```json\n{data_str}\n```\n\n"
        f"Please narrate the findings according to the instructions."
    )
    return system, user_msg


def reason_over_data(
    query: str,
    data: Any,
    endpoint: str,
    complexity: str = "SIMPLE",
    intent: int = 4,
    session_id: Optional[str] = None,
    selected_model_key: Optional[str] = None,
    adapter_resolver: Optional[Callable[[str], Any]] = None,
    get_project_context_by_session: Optional[Callable[[str], Optional[Dict]]] = None,
    model_override: Optional[Tuple[str, str]] = None,
) -> Tuple[str, str, int, int]:
    """Execute reasoning call over API data using Claude on AWS Bedrock."""
    if selected_model_key and selected_model_key != "gemini_brain" and adapter_resolver is not None:
        adapter = adapter_resolver(selected_model_key)
        label = getattr(adapter, "label", selected_model_key)
    elif model_override:
        model_id, label = model_override
        adapter = BedrockAdapter(model_id=model_id, label=label)
    else:
        # Phase 1: Pure function model selector (no LLM call)
        model_id, label = pick_model(intent, data)
        adapter = BedrockAdapter(model_id=model_id, label=label)

    logger.info("GeminiBrain → %s (intent=%s, endpoint=%s)", label, intent, endpoint)

    system, user_msg = _format_payload_and_system(
        query=query,
        data=data,
        endpoint=endpoint,
        session_id=session_id,
        get_project_context_by_session=get_project_context_by_session,
    )

    answer = adapter.converse(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        temperature=0.0,
        max_tokens=400,
    )
    tu = adapter.get_token_usage()
    return (
        answer.strip(),
        label,
        tu.get("input_tokens", 0),
        tu.get("output_tokens", 0),
    )


def reason_over_data_stream(
    query: str,
    data: Any,
    endpoint: str,
    complexity: str = "SIMPLE",
    intent: int = 4,
    session_id: Optional[str] = None,
    selected_model_key: Optional[str] = None,
    adapter_resolver: Optional[Callable[[str], Any]] = None,
    get_project_context_by_session: Optional[Callable[[str], Optional[Dict]]] = None,
    model_override: Optional[Tuple[str, str]] = None,
) -> Generator[Tuple[str, Optional[Tuple[str, int, int]]], None, None]:
    """Stream reasoning tokens over API data using Claude converse_stream.

    Yields
    ------
    Tuple[str, Optional[Tuple[str, int, int]]]
        (token_chunk, final_metadata_or_none)
        When streaming is complete, the last yield contains (full_text, (label, in_tok, out_tok)).
    """
    if selected_model_key and selected_model_key != "gemini_brain" and adapter_resolver is not None:
        adapter = adapter_resolver(selected_model_key)
        label = getattr(adapter, "label", selected_model_key)
    elif model_override:
        model_id, label = model_override
        adapter = BedrockAdapter(model_id=model_id, label=label)
    else:
        model_id, label = pick_model(intent, data)
        adapter = BedrockAdapter(model_id=model_id, label=label)

    logger.info("GeminiBrain streaming → %s (intent=%s, endpoint=%s)", label, intent, endpoint)

    system, user_msg = _format_payload_and_system(
        query=query,
        data=data,
        endpoint=endpoint,
        session_id=session_id,
        get_project_context_by_session=get_project_context_by_session,
    )

    full_chunks = []
    for chunk in adapter.converse_stream(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        temperature=0.0,
        max_tokens=400,
    ):
        full_chunks.append(chunk)
        yield chunk, None

    full_answer = "".join(full_chunks).strip()
    tu = adapter.get_token_usage()
    meta = (label, tu.get("input_tokens", 0), tu.get("output_tokens", 0))
    yield full_answer, meta
