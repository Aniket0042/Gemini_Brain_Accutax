"""
Shared Bedrock client + token tracking for the multi-agent system.
All agents share ONE boto3 client (lazy singleton) and per-request token accounting.
"""

import threading
import boto3
import os
import json
import logging
import time
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger("agents.bedrock")

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
# Primary model (Sonnet) — used for complex multi-step reasoning
MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
)
# Fast/cheap model (Haiku) — used for classification + simple queries
MODEL_ID_FAST = os.getenv(
    "BEDROCK_MODEL_ID_FAST",
    "anthropic.claude-3-haiku-20240307-v1:0",
)
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "ap-south-1")
MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "2000"))

# Cross-region inference prefixes that require toolConfig to be present for plain
# converse calls in ap-south-1 (without it Bedrock returns AccessDeniedException
# "did not allow prompt caching")
_CROSS_REGION_PREFIXES = ("apac.", "us.", "eu.", "global.")
_PASS_THROUGH_TOOL_CONFIG = {
    "tools": [{
        "toolSpec": {
            "name": "passthrough",
            "description": "Fallback tool. Do not call this — always respond with plain text.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                    "required": []
                }
            }
        }
    }],
}

# ──────────────────────────────────────────────
# Singleton client
# ──────────────────────────────────────────────
_client_lock = threading.Lock()
_bedrock_client = None


def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        with _client_lock:
            if _bedrock_client is None:
                _bedrock_client = boto3.client(
                    "bedrock-runtime", region_name=BEDROCK_REGION
                )
    return _bedrock_client


# ──────────────────────────────────────────────
# Per-request token accounting
# ──────────────────────────────────────────────
_tl = threading.local()

# AWS Bedrock pricing per million tokens (USD) — update when rates change
_MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # Claude 3.5 Sonnet v2 (all regional prefixes: apac., eu., us.)
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    # Claude 3.7 Sonnet
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    # Claude 3 Haiku
    "claude-3-haiku":    {"input": 0.25, "output":  1.25},
    # Claude 3.5 Haiku
    "claude-3-5-haiku":  {"input": 0.80, "output":  4.00},
    # Claude 3 Sonnet
    "claude-3-sonnet":   {"input": 3.00, "output": 15.00},
    # Default fallback (Sonnet-tier pricing)
    "_default":          {"input": 3.00, "output": 15.00},
}


def _price_for_model(model_id: str) -> Dict[str, float]:
    """Return the $/M token rates for a given model ID string."""
    mid = model_id.lower()
    for key, rates in _MODEL_PRICING.items():
        if key != "_default" and key in mid:
            return rates
    return _MODEL_PRICING["_default"]


def reset_token_usage():
    """Call at the start of every API request."""
    _tl.total_input = 0
    _tl.total_output = 0
    _tl.calls = 0
    # Per-model breakdown: {model_id: {"input": N, "output": N}}
    _tl.model_tokens: Dict[str, Dict[str, int]] = {}


def add_token_usage(input_tokens: int, output_tokens: int, model_id: str = ""):
    _tl.total_input = getattr(_tl, "total_input", 0) + input_tokens
    _tl.total_output = getattr(_tl, "total_output", 0) + output_tokens
    _tl.calls = getattr(_tl, "calls", 0) + 1
    if model_id:
        if not hasattr(_tl, "model_tokens"):
            _tl.model_tokens = {}
        bucket = _tl.model_tokens.setdefault(model_id, {"input": 0, "output": 0})
        bucket["input"] += input_tokens
        bucket["output"] += output_tokens


def _compute_cost_usd() -> float:
    """Calculate total USD cost from per-model token breakdown.

    Uses per-model token tracking when available; falls back to applying
    the primary MODEL_ID rate to all tokens when no per-model data exists.
    """
    model_tokens: Dict[str, Dict[str, int]] = getattr(_tl, "model_tokens", {})

    if model_tokens:
        total = 0.0
        for mid, counts in model_tokens.items():
            rates = _price_for_model(mid)
            total += (counts["input"]  / 1_000_000) * rates["input"]
            total += (counts["output"] / 1_000_000) * rates["output"]
        return round(total, 6)

    # Fallback: no per-model breakdown — use primary model pricing
    rates = _price_for_model(MODEL_ID)
    inp = getattr(_tl, "total_input", 0)
    out = getattr(_tl, "total_output", 0)
    return round(
        (inp / 1_000_000) * rates["input"] + (out / 1_000_000) * rates["output"],
        6,
    )


def get_token_usage() -> Dict[str, Any]:
    return {
        "input_tokens":  getattr(_tl, "total_input", 0),
        "output_tokens": getattr(_tl, "total_output", 0),
        "llm_calls":     getattr(_tl, "calls", 0),
        "cost_usd":      _compute_cost_usd(),
    }


# ──────────────────────────────────────────────
# Converse helper (text-only, no tool-calling)
# ──────────────────────────────────────────────
def _retry_with_backoff(func, max_retries=3, initial_delay=1.0):
    """Retry a function with exponential backoff for throttling errors."""
    for attempt in range(max_retries):
        try:
            return func()
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code == 'ThrottlingException' and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)
                logger.warning(f"Bedrock throttled (attempt {attempt+1}/{max_retries}), retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise


def converse(
    system_prompt: str,
    messages: List[Dict],
    temperature: float = 0.0,
    max_tokens: int = MAX_TOKENS,
    model_id: Optional[str] = None,
) -> str:
    """Simple text-in / text-out Converse call.  Used by leaf agents."""
    _model = model_id or MODEL_ID
    def _call():
        client = get_bedrock_client()
        kwargs = dict(
            modelId=_model,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        # Cross-region inference profiles (apac.*, us.*, eu.*, global.*) require
        # toolConfig to be present for plain converse calls in ap-south-1,
        # otherwise Bedrock returns AccessDeniedException ("prompt caching").
        if any(_model.startswith(p) for p in _CROSS_REGION_PREFIXES):
            kwargs["toolConfig"] = _PASS_THROUGH_TOOL_CONFIG
        resp = client.converse(**kwargs)
        usage = resp.get("usage", {})
        add_token_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0), model_id=_model)
        content = resp.get("output", {}).get("message", {}).get("content", [])
        # Extract text blocks only; skip any toolUse blocks the model may return
        return "\n".join(b["text"] for b in content if "text" in b)

    return _retry_with_backoff(_call)


# ──────────────────────────────────────────────
# Converse with tool-calling (for Coordinator)
# ──────────────────────────────────────────────
def converse_with_tools(
    system_prompt: str,
    messages: List[Dict],
    tools: List[Dict],
    temperature: float = 0.0,
    max_tokens: int = MAX_TOKENS,
    model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Call Bedrock Converse with toolConfig.
    Returns the full response dict so the caller can inspect
    stopReason ('tool_use' | 'end_turn') and extract tool_use blocks.
    """
    _model = model_id or MODEL_ID
    def _call():
        client = get_bedrock_client()
        kwargs = dict(
            modelId=_model,
            system=[{"text": system_prompt}],
            messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        if tools:
            kwargs["toolConfig"] = {"tools": tools}
        resp = client.converse(**kwargs)
        usage = resp.get("usage", {})
        add_token_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0), model_id=_model)
        return resp
    
    return _retry_with_backoff(_call)


def extract_tool_calls(response: Dict) -> List[Dict]:
    """Pull toolUse blocks out of a Converse response."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    return [b["toolUse"] for b in content if "toolUse" in b]


def extract_text(response: Dict) -> str:
    """Pull all text blocks from a Converse response."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(b["text"] for b in content if "text" in b)
