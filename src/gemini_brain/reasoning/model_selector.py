"""
model_selector.py — Pure function model selection replacing LLM complexity judge.

Phase 1 optimization: Selects the appropriate Bedrock reasoning model (Haiku 4.5 vs Sonnet 3.5)
deterministically based on intent type and payload token size, eliminating a full LLM call (~1.2s).
"""
from __future__ import annotations

import json
from typing import Any, Tuple

from gemini_brain.config.constants import HAIKU45_ID, SONNET35_ID
from gemini_brain.config.settings import settings


def pick_model(intent: int, payload_or_tokens: Any = 0) -> Tuple[str, str]:
    """Pick Bedrock model ID and label based on intent and payload size without an LLM call.

    Parameters
    ----------
    intent : int
        Intent classification (1-7).
        1: FAQ/How-to, 2: Guidance, 3: Reports, 4: Data Query, 5: Forecast, 6: Accounting Concept, 7: Summary & Advice.
    payload_or_tokens : Any
        Either token count (int) or the raw data payload.

    Returns
    -------
    Tuple[str, str]
        (model_id, model_label)
    """
    if isinstance(payload_or_tokens, int):
        tokens = payload_or_tokens
    elif payload_or_tokens is not None:
        try:
            tokens = len(json.dumps(payload_or_tokens, default=str)) // 4
        except Exception:
            tokens = 0
    else:
        tokens = 0

    sonnet_id = getattr(settings, "bedrock_model_id", SONNET35_ID) or SONNET35_ID
    haiku_id = getattr(settings, "bedrock_model_id_fast", HAIKU45_ID) or HAIKU45_ID

    # Route multi-period forecasts (5) and strategic summary/advice (7) or large payloads to Sonnet;
    # everything else routes to ultra-fast Haiku.
    if intent in (5, 7) or tokens > 1200:
        return sonnet_id, "Claude Sonnet 3.5"
    return haiku_id, "Claude Haiku 4.5"
