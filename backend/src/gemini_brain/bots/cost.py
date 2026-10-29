"""cost.py — Monthly cost estimate for a bot draft.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import math
from typing import Dict

from gemini_brain.bots.schema import BotDraft

#: Measured on the VM (docs/ACCUTAX_BOTS_PLAN.md): one VAT review run with the model's summary.
CREDITS_PER_RUN = 90


def _effective_frequency(draft: BotDraft, frequencies: Dict[int, str]) -> str:
    if draft.org_ids:
        if any(frequencies.get(o) == "monthly" for o in draft.org_ids):
            return "monthly"
    return "quarterly"


def _raw_runs_per_month(draft: BotDraft, frequencies: Dict[int, str]) -> float:
    if not draft.schedules:
        return 0.0

    freq = _effective_frequency(draft, frequencies)
    divisor = 1.0 if freq == "monthly" else 3.0
    total = 0.0

    for s in draft.schedules:
        if s.kind == "weekly":
            total += 4.33
        elif s.kind == "monthly":
            total += 1.0
        elif s.kind == "before_deadline":
            total += len(s.days_before) / divisor
        elif s.kind == "after_period_end":
            total += 1.0 / divisor

    return total


def runs_per_month(draft: BotDraft, frequencies: Dict[int, str]) -> int:
    """Estimated number of runs per calendar month."""
    return math.ceil(_raw_runs_per_month(draft, frequencies))


def monthly_credits(draft: BotDraft, frequencies: Dict[int, str]) -> int:
    """Estimated monthly credits (1 credit = $0.001 USD)."""
    raw_runs = _raw_runs_per_month(draft, frequencies)
    if raw_runs == 0:
        return 0
    org_count = max(1, len(draft.org_ids))
    org_multiplier = 1.0 + 0.5 * (org_count - 1)
    cost = raw_runs * CREDITS_PER_RUN * org_multiplier
    return math.ceil(cost)
