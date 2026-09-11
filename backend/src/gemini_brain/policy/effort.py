"""effort.py — what each effort tier actually buys.

The design rule for this file: in a financial assistant, more effort must buy
more *verification of the numbers*, not more elaborate prose. Every field below
is a retrieval or checking budget. None of them change the writing style.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EffortTier:
    name: str
    label: str
    description: str
    #: Allow the LLM planner to run when deterministic routing misses.
    llm_routing: bool
    #: Maximum retrieval calls (API or SQL) for one question.
    max_retrievals: int
    #: Wall-clock budget for the whole retrieval+reasoning phase.
    time_budget_s: int
    #: Check that every number in the prose traces back to retrieved data.
    verify_grounding: bool
    #: Recompute headline figures via the second data source and compare.
    cross_check_sources: bool
    #: Break a compound question into sub-questions and answer each.
    decompose: bool
    #: Require two independent computations to agree before answering.
    dual_compute: bool
    #: Show the plan to the user before the answer.
    show_plan: bool
    #: Preferred model key when the caller did not pin one.
    preferred_model: str
    target_latency: str

    def to_public(self) -> Dict[str, Any]:
        d = asdict(self)
        d["adds"] = self.adds()
        return d

    def adds(self) -> List[str]:
        """Human-readable list of what this tier does beyond the one below."""
        out: List[str] = []
        if self.llm_routing:
            out.append("LLM planning")
        if self.verify_grounding:
            out.append("numeric grounding check")
        if self.decompose:
            out.append("question decomposition")
        if self.cross_check_sources:
            out.append("cross-source verification")
        if self.dual_compute:
            out.append("dual computation with agreement")
        if self.show_plan:
            out.append("visible plan")
        return out


EFFORT_TIERS: Dict[str, EffortTier] = {
    "quick": EffortTier(
        name="quick",
        label="Quick",
        description="Deterministic routing only. Fastest, no verification.",
        llm_routing=False,
        max_retrievals=1,
        time_budget_s=15,
        verify_grounding=False,
        cross_check_sources=False,
        decompose=False,
        dual_compute=False,
        show_plan=False,
        preferred_model="haiku-4.5",
        target_latency="< 1.5s",
    ),
    "standard": EffortTier(
        name="standard",
        label="Standard",
        description="Balanced. Checks that every figure quoted came from the data.",
        llm_routing=True,
        max_retrievals=3,
        time_budget_s=45,
        verify_grounding=True,
        cross_check_sources=False,
        decompose=False,
        dual_compute=False,
        show_plan=False,
        preferred_model="haiku-4.5",
        target_latency="3–6s",
    ),
    "thorough": EffortTier(
        name="thorough",
        label="Thorough",
        description="Breaks the question apart and verifies figures against a second source.",
        llm_routing=True,
        max_retrievals=6,
        time_budget_s=90,
        verify_grounding=True,
        cross_check_sources=True,
        decompose=True,
        dual_compute=False,
        show_plan=True,
        preferred_model="sonnet-3.5",
        target_latency="10–20s",
    ),
    "exhaustive": EffortTier(
        name="exhaustive",
        label="Exhaustive",
        description="Computes each figure two independent ways and reports any disagreement.",
        llm_routing=True,
        max_retrievals=12,
        time_budget_s=180,
        verify_grounding=True,
        cross_check_sources=True,
        decompose=True,
        dual_compute=True,
        show_plan=True,
        preferred_model="sonnet-3.5",
        target_latency="30–90s",
    ),
}

DEFAULT_EFFORT = "exhaustive"
EFFORT_ORDER = ["quick", "standard", "thorough", "exhaustive"]


def resolve_effort(name: Optional[str]) -> EffortTier:
    if not name or name == "auto":
        return EFFORT_TIERS[DEFAULT_EFFORT]
    return EFFORT_TIERS.get(name.lower(), EFFORT_TIERS[DEFAULT_EFFORT])


def escalate(name: str, steps: int = 1) -> str:
    """Return the tier ``steps`` above ``name``, clamped to the top."""
    try:
        idx = EFFORT_ORDER.index(name)
    except ValueError:
        idx = EFFORT_ORDER.index(DEFAULT_EFFORT)
    return EFFORT_ORDER[min(idx + steps, len(EFFORT_ORDER) - 1)]


@dataclass
class ExecutionPolicy:
    """The resolved decision for one request, carried through every stage."""

    model_key: str
    model_id: str
    model_label: str
    effort: EffortTier
    effort_requested: str
    auto: bool = False
    auto_reason: str = ""
    #: Set when the tier could not be delivered in full (e.g. no second source).
    effort_delivered: Optional[str] = None
    degraded_reason: str = ""

    def to_public(self) -> Dict[str, Any]:
        return {
            "model": self.model_key,
            "model_label": self.model_label,
            "effort_requested": self.effort_requested,
            "effort_delivered": self.effort_delivered or self.effort.name,
            "auto": self.auto,
            "auto_reason": self.auto_reason,
            "degraded_reason": self.degraded_reason,
            "verification": {
                "grounding": self.effort.verify_grounding,
                "cross_source": self.effort.cross_check_sources,
                "dual_compute": self.effort.dual_compute,
            },
            "budgets": {
                "max_retrievals": self.effort.max_retrievals,
                "time_budget_s": self.effort.time_budget_s,
            },
        }

    def degrade(self, to_tier: str, reason: str) -> None:
        """Record that the requested tier could not be delivered in full."""
        self.effort_delivered = to_tier
        self.degraded_reason = reason
