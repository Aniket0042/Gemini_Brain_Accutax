"""watches.py — Things a bot can watch, mapped to its engine's rules. Each agent has its own list (bots/agents.py).

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md; per agent since docs/BOT_AGENTS_AND_AP_PLAN.md task A4.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Sequence, Tuple


@dataclass(frozen=True)
class Watch:
    id: str
    label: str            # shown on the card
    side: str             # "purchases" or "sales" (review_vat side)
    rules: Tuple[str, ...]
    default_on: bool
    desc: str = ""        # the line under the label in the builder
    bar: str = ""         # short name for the preview's per-check bars
    #: "document": can be checked on one saved document (the monitor runs it); "state": depends on today's date.
    kind: Literal["document", "state"] = "document"


def _agent(agent: str):
    from gemini_brain.bots import agents
    return agents.get(agent)


def all_watches(agent: str = "vat") -> List[Watch]:
    """All watches available for a bot of this agent."""
    return list(_agent(agent).watches)


def get(watch_id: str, agent: str = "vat") -> Watch:
    """Get a watch by id. Raises KeyError if unknown."""
    for w in _agent(agent).watches:
        if w.id == watch_id:
            return w
    raise KeyError(f"Unknown watch id: {watch_id!r}")


def default_ids(agent: str = "vat") -> List[str]:
    """The watch ids that are on by default."""
    return [w.id for w in _agent(agent).watches if w.default_on]


def watch_for_rule(rule: str, side: str | None = None, agent: str = "vat") -> Watch | None:
    """Find the watch that monitors this rule (Task 3.3 'Why did I get this?')."""
    all_w = _agent(agent).watches
    by_id = {w.id: w for w in all_w}
    if rule in by_id:
        w = by_id[rule]
        if not side or w.side == side:
            return w
    if side:
        for w in all_w:
            if w.side == side and (rule in w.rules or rule == w.id):
                return w
    for w in all_w:
        if rule in w.rules or rule == w.id:
            return w
    return None


def review_calls(watch_ids: Sequence[str], sensitivity: str = "balanced", agent: str = "vat") -> List[Dict[str, Any]]:
    """The engine calls for the selected watches (for VAT: one review_vat call per side that has active rules)."""
    return _agent(agent).engine.calls(watch_ids, sensitivity)
