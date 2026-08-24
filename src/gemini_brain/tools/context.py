"""
context.py — Request context dataclass for Gemini Brain tools and handlers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from gemini_brain.observability.timing import QueryTrace


@dataclass
class RequestCtx:
    """Carries tenant identity and runtime execution context into tool handlers."""
    org_id: int
    user_id: int = 18
    session_id: Optional[str] = None
    allowed_org_ids: Optional[List[int]] = None
    trace: Optional[QueryTrace] = None
    session_state: Optional[dict] = None
    feedback: Optional[str] = None
    api_key: str = ""
    extra: dict = field(default_factory=dict)
