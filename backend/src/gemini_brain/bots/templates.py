"""templates.py — Ready-made bot drafts for one-click creation. Each agent has its own (bots/agents.py).

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md; per agent since docs/BOT_AGENTS_AND_AP_PLAN.md task A4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from gemini_brain.bots.schema import BotDraft


@dataclass(frozen=True)
class Template:
    id: str
    title: str          # card title
    summary: str        # one line under the title
    draft: BotDraft     # org_ids left empty: the server fills the user's current org
    tag: str = ""       # the tag on the card ("Most popular")
    runs: str = ""      # the card's "Runs" line ("Mondays, 08:00")


def all_templates(agent: str = "vat") -> List[Template]:
    """The agent's ready-made templates. Raises KeyError for an unknown agent."""
    from gemini_brain.bots import agents
    return list(agents.get(agent).templates)


def get(template_id: str, agent: Optional[str] = None) -> Template:
    """Get a template by id, from one agent or from any. Raises KeyError if unknown."""
    from gemini_brain.bots import agents
    pool = [agents.get(agent)] if agent else agents.all_agents()
    for a in pool:
        for t in a.templates:
            if t.id == template_id:
                return t
    raise KeyError(f"Unknown template id: {template_id!r}")
