"""
router package — Deterministic Fast Router, Date Resolution, and LLM Tool Router.
"""
from gemini_brain.router.dates import Window, resolve, today
from gemini_brain.router.fast_router import FastRouteResult, fast_route
from gemini_brain.router.llm_router import ToolCallResult, route_with_gemini

__all__ = [
    "Window",
    "resolve",
    "today",
    "FastRouteResult",
    "fast_route",
    "ToolCallResult",
    "route_with_gemini",
]
