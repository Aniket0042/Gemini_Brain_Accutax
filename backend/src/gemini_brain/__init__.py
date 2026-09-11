"""
gemini_brain — Hybrid AI orchestration engine.

Routes financial queries between Google Gemini (classification/routing)
and Anthropic Claude on AWS Bedrock (data-driven reasoning), using the
Accutax REST API as the source of truth for live financial data.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["GeminiBrainRunner", "settings"]


def __getattr__(name: str):
    if name == "settings":
        from gemini_brain.config.settings import settings
        return settings
    if name == "GeminiBrainRunner":
        from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
        return GeminiBrainRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
