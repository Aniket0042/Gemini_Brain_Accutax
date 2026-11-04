"""agents.py — What each bot agent is: its checks, templates, engine, schedule kinds, cost and words for the screens
(docs/BOT_AGENTS_AND_AP_PLAN.md, task A1).

A bot is a user's configured copy of one of these. Every bots module asks this registry instead of assuming VAT.
Agents register themselves from their own module (bots/agents_vat.py); the first lookup loads them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import (TYPE_CHECKING, AbstractSet, Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple)

if TYPE_CHECKING:
    from gemini_brain.bots.schema import BotDraft
    from gemini_brain.bots.templates import Template
    from gemini_brain.bots.watches import Watch


class Engine(Protocol):
    def calls(self, watch_ids: Sequence[str], sensitivity: str) -> List[Dict[str, Any]]:
        """The checks to run, one dict per engine call. Merges the rules of all chosen watches."""

    def run(self, call: Dict[str, Any], *, org_ids: Sequence[int], period: Optional[Dict[str, str]], subject: str,
            deadline: float, exclude: AbstractSet[str], unlimited: bool) -> Dict[str, Any]:
        """One call's result in review_vat's shape: side, period, bills_checked, organizations, groups, findings,
        groups_found, findings_found, groups_by_rule, findings_by_rule."""

    def period(self, draft: "BotDraft", subject: str) -> Optional[Dict[str, str]]:
        """The period a scheduled run checks, or None for the engine's own default window."""

    def position(self, org_ids: Sequence[int], subject: str, deadline: float) -> Any:
        """The raw figures for the box under the intro, as the engine's tool returns them (None when they cannot be
        worked out). `summarize_position` turns them into the box."""

    def summarize_position(self, raw: Any, reviews: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The box under the intro: {"kind": "vat_return" | "payables", ...}, or None."""

    def stake(self, rule: str, detail: Dict[str, Any]) -> float:
        """Money the finding puts at risk; 0 when it is not an amount."""

    def detail(self, item: Dict[str, Any], side: str) -> Dict[str, Any]:
        """What the inbox card keeps for a finding (side, why, level, currency, bills)."""


@dataclass(frozen=True)
class BotAgent:
    key: str                                  # "vat", "ap"
    name: str                                 # "VAT", "Accounts payable"
    watches: Tuple["Watch", ...]
    templates: Tuple["Template", ...]
    schedule_kinds: Tuple[str, ...]           # which of weekly, monthly, daily, before_deadline, after_period_end
    engine: Engine
    credits_per_run: int
    copy: Dict[str, Any]                      # words for the screens (plan task A2)
    columns: Tuple[Dict[str, Any], ...]       # the bills table in the inbox: [{"key": "number", "label": "Bill"}, ...]
    #: The system prompt that turns one sentence into a draft ({today} and {tz} filled in), and the template a
    #: sentence falls back to when the model gives nothing usable. Server side only.
    draft_prompt: str = ""
    fallback_template: str = ""
    #: The Bots home cards for this agent's bots: (bots, findings, user's org ids, subject) -> [{label, value, ...}].
    summary_cards: Optional[Callable[..., List[Dict[str, Any]]]] = field(default=None, compare=False)


_AGENTS: Dict[str, BotAgent] = {}
_loaded = False
_loading = False
#: Every agent module; each registers itself on import.
_MODULES = ("agents_vat", "agents_ap", "agents_ar")


def register(agent: BotAgent) -> None:
    _AGENTS[agent.key] = agent


def _load() -> None:
    global _loaded, _loading
    if not _loaded:
        _loaded = _loading = True
        try:
            import importlib
            for name in _MODULES:
                importlib.import_module(f"gemini_brain.bots.{name}")
        finally:
            _loading = False


def known(key: str) -> bool:
    """Whether a draft may name this agent. While the agent modules load, their own templates' drafts are allowed."""
    _load()
    return key in _AGENTS or _loading


def get(key: str) -> BotAgent:
    """The agent for this key. Raises KeyError for an unknown key."""
    _load()
    if key not in _AGENTS:
        raise KeyError(f"Unknown bot agent: {key!r}")
    return _AGENTS[key]


def all_agents() -> List[BotAgent]:
    _load()
    return list(_AGENTS.values())


def keys() -> List[str]:
    return [a.key for a in all_agents()]


def public(agent: BotAgent) -> Dict[str, Any]:
    """What GET /bots/agents returns: everything except the engine and the card builder."""
    return {
        "key": agent.key,
        "name": agent.name,
        "watches": [{"id": w.id, "label": w.label, "desc": w.desc, "bar": w.bar, "side": w.side, "kind": w.kind,
                     "default_on": w.default_on} for w in agent.watches],
        "templates": [{"id": t.id, "title": t.title, "summary": t.summary, "tag": t.tag, "runs": t.runs}
                      for t in agent.templates],
        "schedule_kinds": list(agent.schedule_kinds),
        "credits_per_run": agent.credits_per_run,
        "copy": agent.copy,
        "columns": [dict(c) for c in agent.columns],
    }
