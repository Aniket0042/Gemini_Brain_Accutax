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
    NARRATION_MAX_TOKENS_SCALAR,
    NARRATION_MAX_TOKENS_TABULAR,
    NARRATION_TABULAR_MIN_ROWS,
    NEVER_EXPOSE_BACKEND_RULE,
    REASONING_MAX_CHARS,
    REASONING_MAX_ITEMS,
)
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.reasoning.model_selector import pick_model

logger = logging.getLogger("gemini_brain.reasoning.claude_reasoner")

# ── Narration System Prompt (Appendix B) ──────────────────────────────────────
#: Rules that hold no matter how much room the answer gets. Kept in one place so
#: the concise and detailed prompts below cannot drift apart — the accuracy rules
#: in particular (never recompute, never infer a missing figure) are what keep
#: narration trustworthy, and they are not negotiable at any length.
#:
#: NOTE: no literal { } braces anywhere in these prompts. gemini_brain_runner
#: used to call .format() on ANALYST_SYSTEM_PROMPT; a brace here would have raised
#: at runtime. Those calls are gone, but keep the constraint — it costs nothing.
_ANALYST_CORE_RULES: str = """You are a financial analyst for Accutax, reporting to a business owner in the UAE.
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
- Format amounts as AED 1,234,567.00.
"""

#: For payloads that are one figure or a handful of fields. A terse answer is the
#: correct answer here, and the word cap is a real latency lever.
ANALYST_SYSTEM_PROMPT: str = _ANALYST_CORE_RULES + """- A table of this data is already displayed above your response. Do not reproduce it.
- Open with the direct answer in one sentence.
- Then at most three short bullets: what stands out, what changed, what to watch.
- Maximum 120 words.
""" + NEVER_EXPOSE_BACKEND_RULE

#: For payloads with rows or groups. The 120-word cap collapses these into a
#: single sentence — a ten-row ranking becomes "revenue was concentrated among a
#: few customers", which is not an answer to the question that was asked. This
#: prompt buys enough room to name what the user asked about, while still leaving
#: the row-by-row detail to the table rendered alongside.
ANALYST_SYSTEM_PROMPT_DETAILED: str = _ANALYST_CORE_RULES + """- A full table of this data is displayed above your response, so do not transcribe
  it row by row. Your job is to make it legible, not to repeat it.
- Open with the direct answer in one sentence.
- Then name the groups, categories or buckets the question was about, each with
  its figure. Never collapse a breakdown into a single total — if the user asked
  which categories, which customers, or which months, they need those named.
- For a long ranking, cover the leaders individually and characterise the rest in
  one line (how much of the total they represent, whether the tail is even).
- Close with what stands out and what to watch, in at most three short bullets.
- Use markdown headers and bullets for structure. Do not build a markdown table.
- Be complete but not padded. Stop when the question is answered — roughly 350
  words is plenty, and shorter is better when the data is simple.
""" + NEVER_EXPOSE_BACKEND_RULE


#: Keys that conventionally hold the row collection in an Accutax payload.
_ROW_KEYS = ("items", "results", "data", "rows", "records")


def _is_breakdown(value: Any) -> bool:
    """True when a nested value is a group breakdown rather than metadata.

    A list of at least NARRATION_TABULAR_MIN_ROWS entries qualifies; so does a
    dict of that many numeric values (monthly totals, aging buckets). A dict of
    three strings is metadata and does not.
    """
    if isinstance(value, list):
        return len(value) >= NARRATION_TABULAR_MIN_ROWS
    if isinstance(value, dict) and len(value) >= NARRATION_TABULAR_MIN_ROWS:
        numeric = sum(1 for v in value.values() if isinstance(v, (int, float)) and not isinstance(v, bool))
        return numeric >= NARRATION_TABULAR_MIN_ROWS
    return False


def classify_payload_shape(data: Any) -> str:
    """Return "tabular" or "scalar" — how much room this answer needs.

    "tabular" means the payload carries rows or groups the user will expect named
    individually (a ranking, a category breakdown, a month-by-month series).
    "scalar" means one figure or a handful of fields, where a terse answer is the
    better answer.

    Errs toward "scalar": that keeps today's fast, cheap narration as the default,
    so only payloads that clearly need the room pay for it.
    """
    if isinstance(data, list):
        return "tabular" if len(data) >= NARRATION_TABULAR_MIN_ROWS else "scalar"

    if isinstance(data, dict):
        for key in _ROW_KEYS:
            value = data.get(key)
            if isinstance(value, list) and len(value) >= NARRATION_TABULAR_MIN_ROWS:
                return "tabular"
        # Reports rarely use a conventional row key — a P&L carries its monthly
        # lines under its own name. Any nested breakdown counts.
        for value in data.values():
            if _is_breakdown(value):
                return "tabular"

    return "scalar"


def narration_budget(shape: str) -> Tuple[str, int]:
    """Map a payload shape to its (system_prompt, max_output_tokens)."""
    if shape == "tabular":
        return ANALYST_SYSTEM_PROMPT_DETAILED, NARRATION_MAX_TOKENS_TABULAR
    return ANALYST_SYSTEM_PROMPT, NARRATION_MAX_TOKENS_SCALAR


def _format_payload_and_system(
    query: str,
    data: Any,
    endpoint: str,
    session_id: Optional[str] = None,
    get_project_context_by_session: Optional[Callable[[str], Optional[Dict]]] = None,
    shape: Optional[str] = None,
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

    system, _ = narration_budget(shape or classify_payload_shape(data))

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

    shape = classify_payload_shape(data)
    _, max_tokens = narration_budget(shape)
    logger.info("Narration shape=%s budget=%d tokens (endpoint=%s)", shape, max_tokens, endpoint)

    system, user_msg = _format_payload_and_system(
        query=query,
        data=data,
        endpoint=endpoint,
        session_id=session_id,
        get_project_context_by_session=get_project_context_by_session,
        shape=shape,
    )

    answer = adapter.converse(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        temperature=0.0,
        max_tokens=max_tokens,
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

    shape = classify_payload_shape(data)
    _, max_tokens = narration_budget(shape)
    logger.info("Narration shape=%s budget=%d tokens (endpoint=%s)", shape, max_tokens, endpoint)

    system, user_msg = _format_payload_and_system(
        query=query,
        data=data,
        endpoint=endpoint,
        session_id=session_id,
        get_project_context_by_session=get_project_context_by_session,
        shape=shape,
    )

    full_chunks = []
    for chunk in adapter.converse_stream(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user_msg}]}],
        temperature=0.0,
        max_tokens=max_tokens,
    ):
        full_chunks.append(chunk)
        yield chunk, None

    full_answer = "".join(full_chunks).strip()
    tu = adapter.get_token_usage()
    meta = (label, tu.get("input_tokens", 0), tu.get("output_tokens", 0))
    yield full_answer, meta
