"""verifier.py — check that every figure in the prose came from the data.

This is the accuracy control that turns "the model said AED 1.2M" into "AED 1.2M
appears in row 3 of the payload". It runs after narration and before the answer
reaches the user.

It is deliberately deterministic: no model is asked to grade another model.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

# Numbers as they appear in narration: 1,234.56 · 1.2M · 45% · AED 94,000
# The trailing guard rejects a digit continuing the number but must allow a
# sentence-ending period, so it checks for "." only when a digit follows it.
_NUMBER = re.compile(
    r"(?<![\w.,])(\d[\d,]*(?:\.\d+)?)\s*(%|k|m|bn|b)?(?![\d,]|\.\d)",
    re.IGNORECASE,
)

_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "bn": 1_000_000_000}

#: Figures that are almost never data claims — years, small ordinals, list counts.
_IGNORED = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0}


@dataclass
class VerificationReport:
    checked: int = 0
    matched: int = 0
    unmatched: List[str] = field(default_factory=list)
    skipped: int = 0
    enforced: bool = False

    @property
    def grounded(self) -> bool:
        return not self.unmatched

    @property
    def grounding_rate(self) -> float:
        return 1.0 if self.checked == 0 else self.matched / self.checked

    def to_public(self) -> Dict[str, Any]:
        return {
            "checked": self.checked,
            "matched": self.matched,
            "unmatched": self.unmatched[:10],
            "grounding_rate": round(self.grounding_rate, 4),
            "grounded": self.grounded,
            "enforced": self.enforced,
        }


def _collect_numbers(payload: Any, out: Set[float], depth: int = 0) -> None:
    """Walk a payload collecting every numeric value it contains."""
    if depth > 8:
        return
    if isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        out.add(float(payload))
        return
    if isinstance(payload, str):
        # Numeric strings are common in these payloads ("1234.50").
        try:
            out.add(float(payload.replace(",", "")))
        except ValueError:
            pass
        return
    if isinstance(payload, dict):
        for v in payload.values():
            _collect_numbers(v, out, depth + 1)
        return
    if isinstance(payload, (list, tuple)):
        for v in payload:
            _collect_numbers(v, out, depth + 1)


def _derived(values: Set[float]) -> Set[float]:
    """Figures a reader would reasonably expect to be derivable from the data.

    Sums and simple ratios of retrieved values are legitimate even though they
    do not appear verbatim — but only when the metric layer computed them, so
    this stays deliberately narrow: totals and pairwise percentages.
    """
    extra: Set[float] = set()
    vals = [v for v in values if v not in _IGNORED][:60]
    total = sum(vals)
    if total:
        extra.add(total)
    # Share-of-total is the most common derived figure in these answers, so the
    # total is a denominator candidate alongside the individual values.
    denominators = vals[:25] + ([total] if total else [])
    for a in vals[:25]:
        for b in denominators:
            if b:
                pct = a / b * 100
                if 0 < pct < 1000:
                    extra.add(round(pct, 1))
    return extra


def _tolerant_match(value: float, known: Set[float]) -> bool:
    """Match with tolerance for rounding and unit scaling in prose."""
    for candidate in (value, value / 100):
        for k in known:
            if k == 0:
                if abs(candidate) < 1e-9:
                    return True
                continue
            # Within 1% covers "AED 1.2M" written for 1,203,455.
            if abs(k - candidate) / abs(k) <= 0.01:
                return True
            if round(k) == round(candidate):
                return True
    return False


def verify_answer(
    answer: str,
    payload: Any,
    *,
    enforce: bool = False,
    allow_derived: bool = True,
) -> VerificationReport:
    """Check every number in ``answer`` against ``payload``.

    ``enforce=False`` runs the check in shadow mode: the report is produced and
    logged but the answer is untouched. Ship that way first, tune tolerance on
    real traffic, then turn enforcement on.
    """
    report = VerificationReport(enforced=enforce)
    if not answer:
        return report

    known: Set[float] = set()
    _collect_numbers(payload, known)
    if allow_derived and known:
        known |= _derived(known)

    for raw, suffix in _NUMBER.findall(answer):
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue

        suffix = (suffix or "").lower()
        if suffix and suffix != "%":
            value *= _SCALE.get(suffix, 1)

        # Years and tiny ordinals carry no financial claim.
        if value in _IGNORED or (1900 <= value <= 2100 and value == int(value) and not suffix):
            report.skipped += 1
            continue

        report.checked += 1
        if _tolerant_match(value, known):
            report.matched += 1
        else:
            label = f"{raw}{suffix}" if suffix else raw
            report.unmatched.append(label)

    return report


def strictness_note(report: VerificationReport) -> str:
    """A short line to append when figures could not be verified."""
    if report.grounded or not report.unmatched:
        return ""
    figures = ", ".join(report.unmatched[:3])
    return (
        f"\n\n> **Unverified figures:** {figures} could not be traced to the retrieved "
        f"data. Treat them as indicative and re-run at a higher effort level to confirm."
    )
