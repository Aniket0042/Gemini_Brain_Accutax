"""Reasoning sub-package — Bedrock adapter, model selector, and Claude reasoner."""
from __future__ import annotations

from gemini_brain.reasoning.bedrock_client import BedrockAdapter, get_bedrock_client
from gemini_brain.reasoning.claude_reasoner import reason_over_data, reason_over_data_stream, ANALYST_SYSTEM_PROMPT
from gemini_brain.reasoning.complexity_judge import judge_complexity
from gemini_brain.reasoning.model_selector import pick_model

__all__ = [
    "BedrockAdapter",
    "get_bedrock_client",
    "reason_over_data",
    "reason_over_data_stream",
    "ANALYST_SYSTEM_PROMPT",
    "judge_complexity",
    "pick_model",
]
