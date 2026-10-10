"""
gemini_brain — Accutax AI backend.

Answers financial questions with a tool-using agent on AWS Bedrock that reads
figures from the Cube semantic layer, VAT law from the knowledge base and
how-to steps from the Accutax app guide.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["settings"]


def __getattr__(name: str):
    if name == "settings":
        from gemini_brain.config.settings import settings
        return settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
