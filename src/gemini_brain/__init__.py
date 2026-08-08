"""
gemini_brain — Hybrid AI orchestration engine.

Routes financial queries between Google Gemini (classification/routing)
and Anthropic Claude on AWS Bedrock (data-driven reasoning), using the
Accutax REST API as the source of truth for live financial data.
"""
from __future__ import annotations

from gemini_brain.config.settings import settings
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner

__version__ = "0.1.0"
__all__ = ["GeminiBrainRunner", "settings"]
