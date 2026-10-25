"""
profiles.py — The specialist agents: one tool loop, a profile per agent.

A profile names an agent, says what its job is, and limits the tools it may call
(docs/ACCUTAX_AGENTS_DESIGN.md, phase 0). Every profile runs through the same
run_agent() with the same guards; only the tools offered and a few lines of the
system prompt change. "accutax" is the general agent and keeps every tool.

Profiles live in code for now. Turning them on per organization, routines and
findings need tables (phases 1 and 2) and are not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

DEFAULT_KEY = "accutax"


@dataclass(frozen=True)
class AgentProfile:
    key: str
    name: str
    #: One line for the agent card: what it does.
    role: str
    #: Tools it may call; None means every tool (the general agent).
    tools: Optional[FrozenSet[str]]
    #: Added to the system prompt after the shared rules.
    instructions: str = ""
    #: The question "Run now" sends.
    run_prompt: str = ""
    #: What the card says "Run now" does.
    run_label: str = ""
    #: Example questions for the card.
    suggestions: Tuple[str, ...] = ()
    #: A lucide icon name for the card.
    icon: str = "sparkles"
    #: The routines this agent will run on its own once phase 1 is built; shown on the card as "coming".
    routines: Tuple[str, ...] = field(default_factory=tuple)

    def allows(self, tool: str) -> bool:
        return self.tools is None or tool in self.tools

    def to_public(self) -> Dict[str, Any]:
        return {"key": self.key, "name": self.name, "role": self.role, "icon": self.icon,
                "tools": sorted(self.tools) if self.tools is not None else [],
                "run_prompt": self.run_prompt, "run_label": self.run_label,
                "suggestions": list(self.suggestions), "routines": list(self.routines),
                "default": self.key == DEFAULT_KEY}


_PROFILES: Tuple[AgentProfile, ...] = (
    AgentProfile(
        key=DEFAULT_KEY,
        name="Accutax AI",
        role="Answers any question about your books, VAT law and the Accutax app.",
        tools=None,
        icon="sparkles",
    ),
    AgentProfile(
        key="vat",
        name="VAT Agent",
        role="Reviews your bills for duplicates and VAT problems, shows your VAT return position and answers UAE VAT law.",
        tools=frozenset({"review_vat", "vat_return", "vat_penalty", "search_vat_kb", "list_documents",
                         "read_document"}),
        instructions=(
            "You are the VAT Agent. Your job is VAT: the review of bills and sales invoices for duplicates and VAT "
            "problems, the VAT return position for a period, penalties, deadlines and UAE VAT law. For a general "
            "check of the books, call review_vat (mode all) and vat_return (the current period) in the same turn. "
            "Give the VAT return position in one or two sentences (period, output VAT, input VAT, net, due date), never "
            "as a table: a review card shows under your answer, and tables are removed from answers that have one."
        ),
        run_prompt="Run a full VAT review of my bills for this year to date, and show my VAT return position for the current period.",
        run_label="Full VAT review and return position",
        suggestions=("Any duplicate bills this quarter?", "When is my next VAT return due?",
                     "Is the VAT on my sales invoices right?"),
        icon="receipt",
        routines=("Weekly VAT review digest", "VAT return reminder 14 and 7 days before the due date"),
    ),
    AgentProfile(
        key="ap",
        name="AP / Bills Agent",
        role="Finds duplicate and odd bills, and checks the bills you upload against the books.",
        tools=frozenset({"review_vat", "list_documents", "read_document", "search_vat_kb"}),
        instructions=(
            "You are the AP / Bills Agent. Your job is supplier bills: duplicates, odd bills, and checking an uploaded "
            "bill or statement against the books. For a general check, call review_vat with mode duplicates first. "
            "When a file is attached, check it against the books with list_documents before saying whether it is "
            "recorded."
        ),
        run_prompt="Check my bills for this year to date for duplicates and anything odd.",
        run_label="Duplicate and odd-bill scan",
        suggestions=("Upload a bill and ask: is this already in the books?",
                     "Any bills with no supplier name or number?", "Show unusually large bills this month"),
        icon="file-check",
        routines=("Check every uploaded bill", "Weekly duplicate scan"),
    ),
    AgentProfile(
        key="cash",
        name="Cash Agent",
        role="Shows your cash position and a weekly cash forecast, with overdue amounts to chase or pay.",
        tools=frozenset({"cash_forecast", "query_metrics", "list_documents"}),
        instructions=(
            "You are the Cash Agent. Your job is cash: the cash position, the weekly forecast, expected collections "
            "and payments, and overdue receivables and payables. For a cash question, call cash_forecast first. A "
            "card under your answer shows the weekly closing cash as a chart and the key figures as tiles, so write "
            "no weekly table: give the lowest week, the closing cash, the overdue amounts and one next step."
        ),
        run_prompt="Show my cash forecast for the next 8 weeks, including overdue amounts to collect and to pay.",
        run_label="8-week cash outlook",
        suggestions=("Which customers owe us the most overdue?", "Will cash go below zero in the next 3 months?",
                     "What is my cash position today?"),
        icon="wallet",
        routines=("Weekly cash outlook", "Alert when the forecast drops below a floor you set"),
    ),
)

_BY_KEY: Dict[str, AgentProfile] = {p.key: p for p in _PROFILES}


class UnknownAgent(ValueError):
    pass


def all_profiles() -> List[AgentProfile]:
    return list(_PROFILES)


def get(key: Optional[str]) -> AgentProfile:
    """The profile for a key; the general agent for None or "". UnknownAgent for any other key."""
    if not key:
        return _BY_KEY[DEFAULT_KEY]
    profile = _BY_KEY.get(str(key).strip().lower())
    if profile is None:
        raise UnknownAgent(f"Unknown agent {key!r}")
    return profile


def resolve(key: Optional[str], thread_key: Optional[str] = None) -> AgentProfile:
    """The agent a request asks for, else the thread's last one, else the general agent. Never raises:
    a thread key that no longer exists falls back to the general agent."""
    if key:
        return get(key)
    try:
        return get(thread_key)
    except UnknownAgent:
        return _BY_KEY[DEFAULT_KEY]
