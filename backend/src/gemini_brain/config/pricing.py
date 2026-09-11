"""
pricing.py — Token pricing tables and cost calculators.

Extracted from:
  - gemini_brain_adapter.py  lines 511-519  (Gemini Brain _cost formula)
  - bedrock_client.py        lines 77-99    (Bedrock per-model pricing table)

Both pricing systems are preserved exactly.  The Gemini Brain formula is the
one used by the orchestrator; the Bedrock pricing table is used internally by
the BedrockAdapter for its own cost reporting.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────
# Gemini pricing (hardcoded in original _cost() — $/M tokens)
# ─────────────────────────────────────────────────────────────
GEMINI_INPUT_PRICE: float = 0.15   # $ per million input tokens
GEMINI_OUTPUT_PRICE: float = 0.60  # $ per million output tokens


# ─────────────────────────────────────────────────────────────
# Bedrock per-model pricing ($/M tokens)
# Keys are model-ID substrings matched against the full model ID.
# ─────────────────────────────────────────────────────────────
BEDROCK_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku":    {"input": 0.25, "output":  1.25},
    "claude-3-5-haiku":  {"input": 0.80, "output":  4.00},
    "claude-3-sonnet":   {"input": 3.00, "output": 15.00},
    "_default":          {"input": 3.00, "output": 15.00},
}


def bedrock_price_for_model(model_id: str) -> dict[str, float]:
    """Return the $/M-token rates for a Bedrock model ID string.

    Iterates the pricing table looking for a substring match; falls back to
    the ``_default`` entry (Sonnet-tier pricing).

    Preserved exactly from ``bedrock_client._price_for_model``.
    """
    mid = model_id.lower()
    for key, rates in BEDROCK_MODEL_PRICING.items():
        if key != "_default" and key in mid:
            return rates
    return BEDROCK_MODEL_PRICING["_default"]


def gemini_brain_cost(
    gemini_input: int,
    gemini_output: int,
    bedrock_input: int,
    bedrock_output: int,
    bedrock_model_id: str,
) -> float:
    """Calculate total USD cost for a single Gemini Brain run.

    This is the *exact* formula from the original ``GeminiBrainRunner._cost``
    static method (gemini_brain_adapter.py lines 511-519).  The Gemini portion
    uses fixed Flash pricing; the Bedrock portion branches on model-ID substring.

    Parameters
    ----------
    gemini_input : int
        Total Gemini input tokens across all calls in this run.
    gemini_output : int
        Total Gemini output tokens.
    bedrock_input : int
        Total Bedrock input tokens.
    bedrock_output : int
        Total Bedrock output tokens.
    bedrock_model_id : str
        The Bedrock model ID used for the reasoning step (determines pricing tier).
    """
    # Gemini cost (Flash pricing)
    gc = (gemini_input / 1e6) * GEMINI_INPUT_PRICE + (gemini_output / 1e6) * GEMINI_OUTPUT_PRICE

    # Bedrock cost — branching on model substring (original behaviour preserved)
    if "haiku-20240307" in bedrock_model_id:
        bc = (bedrock_input / 1e6) * 0.25 + (bedrock_output / 1e6) * 1.25
    elif "haiku-4-5" in bedrock_model_id:
        bc = (bedrock_input / 1e6) * 0.80 + (bedrock_output / 1e6) * 4.00
    else:
        bc = (bedrock_input / 1e6) * 3.00 + (bedrock_output / 1e6) * 15.00

    return round(gc + bc, 6)
