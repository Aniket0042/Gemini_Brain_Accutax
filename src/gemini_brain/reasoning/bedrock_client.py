"""
bedrock_client.py — AWS Bedrock client singleton and BedrockAdapter wrapper.

Combines bedrock_adapter.py and bedrock_client.py into a single, clean module.
Tracks tokens per adapter instance, implements exponential backoff on throttling,
supports Bedrock prompt caching (cachePoint), and handles cross-region inference profiles.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Generator, List, Optional, Union

import boto3
from botocore.exceptions import ClientError

from gemini_brain.config.constants import (
    CACHE_SUPPORTED_MODELS,
    CROSS_REGION_PREFIXES,
    PASS_THROUGH_TOOL_CONFIG,
)
from gemini_brain.config.pricing import bedrock_price_for_model
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.reasoning.bedrock_client")

# ── Singleton boto3 Bedrock client ───────────────────────────────────────────
_client_lock = threading.Lock()
_bedrock_client: Optional[Any] = None


def get_bedrock_client(region: str = "") -> Any:
    """Return lazy singleton boto3 bedrock-runtime client."""
    global _bedrock_client
    r = region or settings.bedrock_region
    if _bedrock_client is None:
        with _client_lock:
            if _bedrock_client is None:
                _bedrock_client = boto3.client("bedrock-runtime", region_name=r)
    return _bedrock_client


def retry_with_backoff(func: Any, max_retries: int = 3, initial_delay: float = 1.0) -> Any:
    """Retry func with exponential backoff on AWS ThrottlingException."""
    for attempt in range(max_retries):
        try:
            return func()
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ThrottlingException" and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.warning(
                    "Bedrock throttled (attempt %d/%d), retrying in %.1fs...",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
            else:
                raise


def extract_tool_calls(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull toolUse blocks out of a Bedrock Converse response dict."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    return [b["toolUse"] for b in content if "toolUse" in b]


def extract_text(response: Dict[str, Any]) -> str:
    """Pull all text blocks from a Bedrock Converse response dict."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(b["text"] for b in content if "text" in b)


def _extract_text_with_passthrough_fallback(content: List[Dict[str, Any]]) -> str:
    """Pull text blocks from Converse content, falling back to whatever tool
    call the model made if it answered via `toolUse` instead of plain text.

    Cross-region model IDs always carry PASS_THROUGH_TOOL_CONFIG (see
    _call() below) even though its description tells the model never to call
    it. Claude sometimes calls it anyway — especially for JSON-shaped answers
    like intent classification — and puts the real answer in `input.note`
    instead of a text block, which the plain text-block join otherwise misses
    entirely (empty string despite real output tokens spent).

    A second, related case: when the system prompt itself documents a catalog
    of real tool names (e.g. the endpoint router's tool list), Claude can call
    `toolUse` with one of *those* names and real parameters — e.g.
    {"name": "purchases_by_vendor", "input": {"period": "2025"}} — even though
    only "passthrough" was actually declared via toolConfig. That's a better
    answer than the passthrough note, not a malformed one: re-serialize it as
    {"name":..., "parameters":...} JSON, the exact shape callers like
    parse_function_call() already know how to parse.
    """
    text = "\n".join(b["text"] for b in content if "text" in b)
    if text:
        return text
    for b in content:
        tool_use = b.get("toolUse")
        if not tool_use:
            continue
        name = tool_use.get("name")
        tool_input = tool_use.get("input") or {}
        if name == "passthrough":
            note = tool_input.get("note")
            if note:
                return str(note)
            continue
        return json.dumps({"name": name, "parameters": tool_input})
    return text


# ── BedrockAdapter class ─────────────────────────────────────────────────────

class BedrockAdapter:
    """LLM adapter for any AWS Bedrock Claude model.

    Tracks token usage per instance and implements prompt caching where supported.
    """

    _cache_point_enabled: bool = True

    def __init__(self, model_id: str, label: str = ""):
        self.model_id = model_id
        self.label = label or model_id
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0
        self._cache_read_tokens = 0
        self._cache_write_tokens = 0

    def reset_tokens(self) -> None:
        """Reset internal token counters."""
        self._input_tokens = 0
        self._output_tokens = 0
        self._calls = 0
        self._cache_read_tokens = 0
        self._cache_write_tokens = 0

    def _build_system_array(self, system_prompt: Union[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Build Bedrock system array with optional cachePoint block."""
        if isinstance(system_prompt, list):
            return system_prompt
        supports = (
            BedrockAdapter._cache_point_enabled
            and any(m in self.model_id.lower() for m in CACHE_SUPPORTED_MODELS)
            and not any(self.model_id.startswith(p) for p in CROSS_REGION_PREFIXES)
        )
        if supports:
            return [
                {"text": system_prompt},
                {"cachePoint": {"type": "default"}},
            ]
        return [{"text": system_prompt}]

    def _track_usage(self, usage: Dict[str, Any]) -> None:
        self._input_tokens += usage.get("inputTokens") or 0
        self._output_tokens += usage.get("outputTokens") or 0
        self._cache_read_tokens += usage.get("cacheReadInputTokens") or 0
        self._cache_write_tokens += usage.get("cacheWriteInputTokens") or 0
        self._calls += 1

    def converse(
        self,
        system_prompt: Union[str, List[Dict[str, Any]]],
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = settings.bedrock_max_tokens,
    ) -> str:
        """Text-in / text-out call (no tool calling)."""
        system = self._build_system_array(system_prompt)

        def _call():
            client = get_bedrock_client()
            kwargs: Dict[str, Any] = dict(
                modelId=self.model_id,
                system=system,
                messages=messages,
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
            if any(self.model_id.startswith(p) for p in CROSS_REGION_PREFIXES):
                kwargs["toolConfig"] = PASS_THROUGH_TOOL_CONFIG
            resp = client.converse(**kwargs)
            self._track_usage(resp.get("usage", {}))
            content = resp.get("output", {}).get("message", {}).get("content", [])
            return _extract_text_with_passthrough_fallback(content)

        try:
            return retry_with_backoff(_call)
        except Exception as e:
            err = str(e).lower()
            if "cachepoint" in err or "cache_point" in err or "prompt caching" in err:
                BedrockAdapter._cache_point_enabled = False
                logger.warning("Prompt caching not supported for %s — disabling", self.model_id)
                plain = system_prompt if isinstance(system_prompt, str) else ""
                system[:] = [{"text": plain}]
                return retry_with_backoff(_call)
            raise

    def converse_stream(
        self,
        system_prompt: Union[str, List[Dict[str, Any]]],
        messages: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = settings.bedrock_max_tokens,
    ) -> Generator[str, None, None]:
        """Stream text tokens using Bedrock converse_stream API."""
        system = self._build_system_array(system_prompt)
        client = get_bedrock_client()
        kwargs: Dict[str, Any] = dict(
            modelId=self.model_id,
            system=system,
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        if any(self.model_id.startswith(p) for p in CROSS_REGION_PREFIXES):
            kwargs["toolConfig"] = PASS_THROUGH_TOOL_CONFIG

        try:
            resp = client.converse_stream(**kwargs)
        except Exception as e:
            err = str(e).lower()
            if "cachepoint" in err or "cache_point" in err or "prompt caching" in err:
                BedrockAdapter._cache_point_enabled = False
                logger.warning("Prompt caching not supported for %s — disabling", self.model_id)
                plain = system_prompt if isinstance(system_prompt, str) else ""
                kwargs["system"] = [{"text": plain}]
                resp = client.converse_stream(**kwargs)
            else:
                raise

        stream = resp.get("stream")
        if stream:
            for event in stream:
                if "contentBlockDelta" in event:
                    delta = event["contentBlockDelta"].get("delta", {})
                    if "text" in delta:
                        yield delta["text"]
                elif "metadata" in event:
                    usage = event["metadata"].get("usage", {})
                    self._track_usage(usage)

    def converse_with_tools(
        self,
        system_prompt: Union[str, List[Dict[str, Any]]],
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = settings.bedrock_max_tokens,
    ) -> Dict[str, Any]:
        """Tool-calling Converse call. Returns Bedrock-native response dict."""
        system = self._build_system_array(system_prompt)

        def _call():
            client = get_bedrock_client()
            kwargs: Dict[str, Any] = dict(
                modelId=self.model_id,
                system=system,
                messages=messages,
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
            if tools:
                kwargs["toolConfig"] = {"tools": tools}
            resp = client.converse(**kwargs)
            self._track_usage(resp.get("usage", {}))
            return resp

        try:
            return retry_with_backoff(_call)
        except Exception as e:
            err = str(e).lower()
            if "cachepoint" in err or "cache_point" in err or "prompt caching" in err:
                BedrockAdapter._cache_point_enabled = False
                logger.warning("Prompt caching not supported for %s — disabling", self.model_id)
                plain = system_prompt if isinstance(system_prompt, str) else ""
                system[:] = [{"text": plain}]
                return retry_with_backoff(_call)
            raise

    def get_token_usage(self) -> Dict[str, Any]:
        """Return token counts and calculated cost for this adapter instance."""
        rates = bedrock_price_for_model(self.model_id)
        cost = (
            (self._input_tokens / 1_000_000) * rates["input"]
            + (self._cache_write_tokens / 1_000_000) * rates["input"] * 1.25
            + (self._cache_read_tokens / 1_000_000) * rates["input"] * 0.10
            + (self._output_tokens / 1_000_000) * rates["output"]
        )
        result: Dict[str, Any] = {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens,
            "llm_calls": self._calls,
            "cost_usd": round(cost, 6),
        }
        if self._cache_read_tokens > 0 or self._cache_write_tokens > 0:
            result["cache_read_tokens"] = self._cache_read_tokens
            result["cache_write_tokens"] = self._cache_write_tokens
            all_input = (
                self._input_tokens + self._cache_read_tokens + self._cache_write_tokens
            )
            uncached_cost = (
                (all_input / 1_000_000) * rates["input"]
                + (self._output_tokens / 1_000_000) * rates["output"]
            )
            result["cache_savings_usd"] = round(uncached_cost - cost, 6)
        return result
