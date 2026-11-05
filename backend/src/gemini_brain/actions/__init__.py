"""Write actions: records the assistant prepares in Accutax (draft invoices and quotes) that the person confirms.

The model never writes. A write tool only prepares a pending action (pending.py) and a card built in code; the
write itself runs when the person clicks confirm, as that person, through the Accutax API (accutax_api/client.py).
Each step leaves an audit line (audit.py). See Alaan_Wafeq_AI_Features/Invoice and Quote by Chat - Implementation
Plan.md.

Switches: AI_WRITE_ACTIONS_ENABLED (off by default), AI_WRITE_TOOLS (which tools), AI_WRITE_USERS (who).
"""
from __future__ import annotations

from typing import Any, FrozenSet, Optional

from gemini_brain.config.settings import settings

#: Every write tool the assistant has. A tool not listed here is never treated as a write tool.
WRITE_TOOLS: FrozenSet[str] = frozenset({"draft_sales_document", "copy_sales_document", "create_customer"})


def _listed(raw: str) -> set:
    return {p.strip().lower() for p in (raw or "").split(",") if p.strip()}


def enabled_for(user_id: Any = None, email: Optional[str] = None) -> bool:
    """Whether this person may use write actions: the switch is on and, when AI_WRITE_USERS lists people, their
    email (any case) or id is one of them."""
    if not settings.ai_write_actions_enabled:
        return False
    people = _listed(settings.ai_write_users)
    if not people:
        return True
    return str(user_id).lower() in people or (email or "").strip().lower() in people


def tool_enabled(name: str) -> bool:
    """Whether the write tool `name` is switched on (AI_WRITE_TOOLS empty = all of WRITE_TOOLS)."""
    if not settings.ai_write_actions_enabled or name not in WRITE_TOOLS:
        return False
    chosen = _listed(settings.ai_write_tools)
    return not chosen or name.lower() in chosen
