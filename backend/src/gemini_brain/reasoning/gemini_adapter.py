"""gemini_adapter.py — Google Gemini as a selectable answering model.

Implements the same surface the orchestrator and narrator already call on
``BedrockAdapter`` (``converse``, ``converse_stream``, ``get_token_usage``,
``label``), so a Gemini model can be picked in the UI and actually answer,
rather than appearing in the picker and silently falling back to Bedrock.

Tool-calling (``converse_with_tools``) is deliberately not implemented: the SQL
fallback loop stays on Bedrock, and callers that need tools check for it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Generator, List, Union

from gemini_brain.config.pricing import GEMINI_INPUT_PRICE, GEMINI_OUTPUT_PRICE
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.reasoning.gemini_adapter")


def _flatten_system(system_prompt: Union[str, List[Dict[str, Any]]]) -> str:
    """Accept either a plain string or Bedrock's [{'text': ...}] system array."""
    if isinstance(system_prompt, str):
        return system_prompt
    parts = []
    for block in system_prompt or []:
        if isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def _flatten_messages(messages: List[Dict[str, Any]]) -> str:
    """Collapse the Bedrock message shape into the single prompt Gemini takes."""
    out: List[str] = []
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(
                block["text"]
                for block in (content or [])
                if isinstance(block, dict) and "text" in block
            )
        if not text:
            continue
        role = msg.get("role", "user")
        prefix = "User" if role == "user" else "Assistant"
        out.append(f"{prefix}: {text}")
    return "\n\n".join(out)


class GeminiAdapter:
    """LLM adapter for Google Gemini models."""

    #: Callers that need tool use check this before selecting an adapter.
    supports_tools = False

    def __init__(self, model_id: str, label: str = ""):
        self.model_id = model_id
        self.label = label or model_id
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0

    def _client(self) -> Any:
        key = settings.gemini_api_key
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        from google import genai

        return genai.Client(api_key=key)

    def _record(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            self._input_tokens += getattr(usage, "prompt_token_count", 0) or 0
            self._output_tokens += (
                getattr(usage, "candidates_token_count", 0) or 0
            )
        self._calls += 1

    def converse(
        self,
        system_prompt: Union[str, List[Dict[str, Any]]],
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> str:
        """Text-in / text-out call."""
        from google.genai import types

        response = self._client().models.generate_content(
            model=self.model_id,
            contents=_flatten_messages(messages),
            config=types.GenerateContentConfig(
                system_instruction=_flatten_system(system_prompt) or None,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        self._record(response)
        return response.text or ""

    def converse_stream(
        self,
        system_prompt: Union[str, List[Dict[str, Any]]],
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> Generator[str, None, None]:
        """Stream text chunks as they are generated."""
        from google.genai import types

        stream = self._client().models.generate_content_stream(
            model=self.model_id,
            contents=_flatten_messages(messages),
            config=types.GenerateContentConfig(
                system_instruction=_flatten_system(system_prompt) or None,
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        last = None
        for chunk in stream:
            last = chunk
            if chunk.text:
                yield chunk.text
        if last is not None:
            self._record(last)

    def get_token_usage(self) -> Dict[str, Any]:
        """Token counts and cost for this adapter instance."""
        cost = (
            (self._input_tokens / 1_000_000) * GEMINI_INPUT_PRICE
            + (self._output_tokens / 1_000_000) * GEMINI_OUTPUT_PRICE
        )
        return {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "llm_calls": self._calls,
            "cost_usd": round(cost, 6),
        }
