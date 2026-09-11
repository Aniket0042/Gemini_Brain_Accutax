"""auto.py — pick (model, effort) without spending an LLM call to do it.

Every signal here is cheap to compute from the query text, the resolved intent
and the retrieved payload size. Auto is deliberately a pure function so its
decisions are reproducible, testable, and explainable to the user.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from gemini_brain.policy.effort import (
    DEFAULT_EFFORT,
    EFFORT_ORDER,
    ExecutionPolicy,
    escalate,
    resolve_effort,
)
from gemini_brain.policy.registry import AUTO_KEY, resolve_model

# Language that implies the user wants analysis rather than a lookup.
_ANALYTIC = re.compile(
    r"\b(why|compare|comparison|versus|vs\.?|trend|trending|forecast|project(?:ion|ed)?|"
    r"predict|outlook|risk|driver|explain|breakdown|variance|health|runway|"
    r"should (?:i|we)|recommend)\b",
    re.IGNORECASE,
)

# Multiple metrics in one breath — "revenue and expenses", "P&L and cash flow".
_MULTI_METRIC = re.compile(
    r"\b(and|plus|along with|as well as|together with)\b.*\b("
    r"revenue|expenses?|profit|cash|margin|balance|invoices?|bills?|vat|tax)\b",
    re.IGNORECASE,
)


def _months_spanned(date_from: Optional[str], date_to: Optional[str]) -> int:
    if not date_from or not date_to:
        return 0
    try:
        fy, fm = int(date_from[:4]), int(date_from[5:7])
        ty, tm = int(date_to[:4]), int(date_to[5:7])
    except (ValueError, IndexError):
        return 0
    return max(0, (ty - fy) * 12 + (tm - fm))


def choose_policy(
    query: str,
    *,
    requested_model: Optional[str] = None,
    requested_effort: Optional[str] = None,
    intent: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    payload_tokens: int = 0,
    partial_coverage: bool = False,
    prior_turn_corrected: bool = False,
    budget_constrained: bool = False,
) -> ExecutionPolicy:
    """Resolve the execution policy for one request.

    An explicit model or effort from the caller always wins; auto only fills in
    what was left unset.
    """
    explicit_effort = bool(requested_effort) and requested_effort != AUTO_KEY
    explicit_model = bool(requested_model) and requested_model != AUTO_KEY
    reasons: list[str] = []

    if explicit_effort:
        effort_name = resolve_effort(requested_effort).name
        reasons.append(f"{effort_name} effort requested")
    else:
        effort_name = DEFAULT_EFFORT

        if intent in (5, 7):
            effort_name = escalate(effort_name)
            reasons.append("forecast or strategic question")

        if _ANALYTIC.search(query):
            effort_name = escalate(effort_name)
            reasons.append("question asks for analysis, not a lookup")

        if _MULTI_METRIC.search(query):
            effort_name = escalate(effort_name)
            reasons.append("more than one metric requested")

        months = _months_spanned(date_from, date_to)
        if months > 12:
            effort_name = escalate(effort_name)
            reasons.append(f"spans {months} months")

        if partial_coverage:
            effort_name = escalate(effort_name)
            reasons.append("this data is only partially populated, so figures are cross-checked")

        if prior_turn_corrected:
            effort_name = escalate(effort_name)
            reasons.append("previous answer was corrected")

        if budget_constrained and effort_name in ("thorough", "exhaustive"):
            effort_name = "standard"
            reasons = ["organization is near its usage cap"]

    tier = resolve_effort(effort_name)

    if explicit_model:
        spec = resolve_model(requested_model)
    else:
        spec = resolve_model(tier.preferred_model)
        if payload_tokens > 1200 and "thorough" in spec.efforts:
            spec = resolve_model("sonnet-3.5")
            reasons.append("large result set")

    # A model may not be used above the effort tiers it declares.
    if tier.name not in spec.efforts:
        allowed = [e for e in EFFORT_ORDER if e in spec.efforts]
        if allowed:
            capped = allowed[-1] if EFFORT_ORDER.index(tier.name) > EFFORT_ORDER.index(allowed[-1]) else allowed[0]
            tier = resolve_effort(capped)
            reasons.append(f"{spec.label} supports up to {tier.label.lower()} effort")

    if not reasons:
        reasons.append("straightforward lookup")

    policy = ExecutionPolicy(
        model_key=spec.key,
        model_id=spec.model_id,
        model_label=spec.label,
        effort=tier,
        effort_requested=requested_effort or AUTO_KEY,
        auto=not (explicit_effort and explicit_model),
        auto_reason="; ".join(reasons),
    )
    return policy
