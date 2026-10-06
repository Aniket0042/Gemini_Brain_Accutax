"""
multi_org_conditions.py — Conditions on a figure: "margin above 20%", "negative profit".

The metric path used to understand only top/bottom N, so "which organizations
have profit margin above 20%" listed every organization (demo, 6 Oct 2026).
A condition is read from the question, applied to the computed comparison in
code, and stated in the answer. A condition that cannot be applied is said
plainly; it is never dropped in silence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.orchestrator.multi_org_metrics import BY_KEY, CURRENCY, PERCENT, Metric

_FLAGS = re.IGNORECASE
# (?![\d.]) keeps a number whole, so "90 days" is never read as "9" followed by "0 days".
_NUM = r"(-?\d[\d,]*(?:\.\d+)?)(?![\d.])\s*(%|percent|per\s*cent|k\b|thousand|m\b|mn\b|million|bn\b|billion)?"
_MONEY = r"(?:(?:aed|usd|eur|gbp|inr|sar)\s*)?"
_GT = r"above|over|more\s+than|greater\s+than|higher\s+than|exceed(?:s|ing)?|at\s+least|not\s+less\s+than|no\s+less\s+than"
_LT = r"below|under|less\s+than|lower\s+than|at\s+most|not\s+more\s+than|no\s+more\s+than|up\s+to"
_GTE_WORDS = ("at least", "not less than", "no less than")
_LTE_WORDS = ("at most", "not more than", "no more than", "up to")

#: "over 90 days" is an age or a period, not a threshold on a figure.
_NOT_PERIOD = r"(?!\s*(?:days?|weeks?|months?|quarters?|years?|yrs?)\b)"
_BETWEEN = re.compile(rf"\bbetween\s+{_MONEY}{_NUM}\s*(?:and|to|-)\s*{_MONEY}{_NUM}{_NOT_PERIOD}", _FLAGS)
_WORDED = re.compile(rf"\b(?P<op>{_GT}|{_LT})\s+{_MONEY}{_NUM}{_NOT_PERIOD}", _FLAGS)
_SYMBOL = re.compile(rf"(?P<op>>=|<=|≥|≤|>|<)\s*{_MONEY}{_NUM}{_NOT_PERIOD}", _FLAGS)
_NEGATIVE = re.compile(
    r"\b(negative|in\s+the\s+red|(?:making|made|make|at|running)\s+(?:a\s+)?(?:net\s+)?loss(?:es)?"
    r"|loss[\s-]?making|losing\s+money|unprofitable|deficit)\b", _FLAGS)
_POSITIVE = re.compile(r"\b(positive|profitable|in\s+the\s+black|making\s+(?:a\s+)?profit)\b", _FLAGS)
#: Sign words that name net profit on their own ("loss-making organizations").
_IMPLIES_PROFIT = re.compile(
    r"\b(loss[\s-]?making|losing\s+money|unprofitable|profitable|in\s+the\s+(?:red|black)"
    r"|(?:making|made|at|running)\s+(?:a\s+)?(?:net\s+)?loss(?:es)?)\b", _FLAGS)
#: Relative conditions there is no rule for yet.
_RELATIVE = re.compile(r"\b(above|below|over|under|higher\s+than|lower\s+than)\s+(the\s+)?(average|median|mean|norm)\b",
                       _FLAGS)
_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
_SYMBOL_OPS = {">": "gt", ">=": "gte", "≥": "gte", "<": "lt", "<=": "lte", "≤": "lte"}
_OP_WORDS = {"gt": "above", "gte": "at least", "lt": "below", "lte": "at most"}


@dataclass(frozen=True)
class Condition:
    metric: str                 # metric key, e.g. "profit_margin"
    op: str                     # gt | gte | lt | lte | between
    values: Tuple[float, ...]
    phrase: str                 # the words in the question, for the answer

    def holds(self, value: Optional[float]) -> bool:
        if value is None:
            return False
        if self.op == "gt":
            return value > self.values[0]
        if self.op == "gte":
            return value >= self.values[0]
        if self.op == "lt":
            return value < self.values[0]
        if self.op == "lte":
            return value <= self.values[0]
        low, high = sorted(self.values)
        return low <= value <= high

    def describe(self) -> str:
        metric = BY_KEY[self.metric]
        unit = "%" if metric.unit == PERCENT else ""
        if self.op == "between":
            low, high = sorted(self.values)
            return f"{metric.label} between {_num(low)}{unit} and {_num(high)}{unit}"
        if self.values[0] == 0 and self.op in ("lt", "gt"):
            return f"{metric.label} {'below' if self.op == 'lt' else 'above'} zero"
        return f"{metric.label} {_OP_WORDS[self.op]} {_num(self.values[0])}{unit}"


def _num(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def has_condition(question: str) -> bool:
    """Whether the question puts a condition on a figure."""
    q = question or ""
    return bool(_BETWEEN.search(q) or _WORDED.search(q) or _SYMBOL.search(q)
                or _NEGATIVE.search(q) or _POSITIVE.search(q) or _RELATIVE.search(q))


def implied_metric(question: str) -> Optional[Metric]:
    """Net profit, when the question names it only through words like "loss-making"."""
    return BY_KEY["net_profit"] if _IMPLIES_PROFIT.search(question or "") else None


def _amount(number: str, suffix: Optional[str], metric: Metric) -> Tuple[Optional[float], Optional[str]]:
    """(value, reason it cannot be used)."""
    value = float(number.replace(",", ""))
    suffix = re.sub(r"\s+", "", (suffix or "").lower())
    is_pct = suffix in ("%", "percent")  # "per cent" arrives here as "percent"
    if metric.unit == PERCENT:
        if suffix and not is_pct:
            return None, f"{metric.label} is a percentage, so '{number}{suffix}' cannot be compared with it"
        return value, None
    if is_pct:
        return None, f"{metric.label} is an amount, not a percentage"
    return value * _MULTIPLIERS.get(suffix, 1.0), None


def _target(metrics: Sequence[Metric], question: str, at: int) -> Optional[Metric]:
    """The figure a condition at position `at` refers to: the nearest one named before it, else after it."""
    if len(metrics) == 1:
        return metrics[0]
    spans = []
    for m in metrics:
        hit = m.pattern.search(question)
        if hit:
            spans.append((hit.start(), hit.end(), m))
    before = [s for s in spans if s[1] <= at]
    if before:
        return max(before, key=lambda s: s[1])[2]
    after = [s for s in spans if s[0] >= at]
    return min(after, key=lambda s: s[0])[2] if after else None


def parse_conditions(question: str, metrics: Sequence[Metric]) -> Tuple[List[Condition], List[str]]:
    """Conditions in the question on the given figures, and notes for any that cannot be applied."""
    q = question or ""
    conditions: List[Condition] = []
    notes: List[str] = []
    taken: List[Tuple[int, int]] = []

    def add(span: Tuple[int, int], op: str, raw: Sequence[Tuple[str, Optional[str]]], phrase: str) -> None:
        if any(span[0] < e and s < span[1] for s, e in taken):
            return
        taken.append(span)
        metric = _target(metrics, q, span[0])
        if metric is None:
            notes.append(f"The condition '{phrase}' was not applied: it is not clear which figure it refers to.")
            return
        values = []
        for number, suffix in raw:
            value, reason = _amount(number, suffix, metric)
            if reason:
                notes.append(f"The condition '{phrase}' was not applied: {reason}.")
                return
            values.append(value)
        conditions.append(Condition(metric.key, op, tuple(values), phrase.strip()))

    for m in _BETWEEN.finditer(q):
        add(m.span(), "between", [(m.group(1), m.group(2)), (m.group(3), m.group(4))], m.group(0))
    for m in _WORDED.finditer(q):
        word = re.sub(r"\s+", " ", m.group("op").lower())
        op = "gte" if word in _GTE_WORDS else "lte" if word in _LTE_WORDS else \
            "gt" if re.fullmatch(_GT, word, _FLAGS) else "lt"
        add(m.span(), op, [(m.group(2), m.group(3))], m.group(0))
    for m in _SYMBOL.finditer(q):
        add(m.span(), _SYMBOL_OPS[m.group("op")], [(m.group(2), m.group(3))], m.group(0))
    for m in _NEGATIVE.finditer(q):
        add(m.span(), "lt", [("0", None)], m.group(0))
    for m in _POSITIVE.finditer(q):
        add(m.span(), "gt", [("0", None)], m.group(0))
    for m in _RELATIVE.finditer(q):
        if not any(m.start() < e and s < m.end() for s, e in taken):
            notes.append(f"The condition '{m.group(0)}' was not applied: comparisons with an average are not "
                         "supported yet, so every organization is shown.")
    return conditions, notes


def apply_conditions(comparison: Dict[str, Any], question: str) -> Dict[str, Any]:
    """The comparison cut to the organizations that meet the question's conditions.

    Records what was applied ("condition") and what could not be ("condition_notes"),
    so the answer states both. Totals keep covering every organization, as with top N.
    """
    kind = comparison.get("kind", "metric")
    if kind == "metric":
        keys = [comparison["metric"]]
    elif kind == "multi_metric":
        keys = list(comparison["metrics"])
    else:
        keys = []
    metrics = [BY_KEY[k] for k in keys if k in BY_KEY]
    if not has_condition(question):
        return comparison
    if not metrics:
        return {**comparison, "condition_notes": [
            "Conditions on figures over time are not supported yet, so the question's condition was not applied."]}

    conditions, notes = parse_conditions(question, metrics)
    if not conditions:
        return {**comparison, "condition_notes": notes} if notes else comparison

    def value(row: Dict[str, Any], key: str) -> Optional[float]:
        return row.get("value") if kind == "metric" else (row.get("values") or {}).get(key)

    rows = comparison["rows"]
    kept = [r for r in rows if all(c.holds(value(r, c.metric)) for c in conditions)]
    filtered = dict(comparison)
    filtered["rows"] = kept
    filtered["condition"] = {
        "text": " and ".join(c.describe() for c in conditions),
        "matched": len(kept),
        "of": len(rows),
    }
    if notes:
        filtered["condition_notes"] = notes
    if any(BY_KEY[c.metric].unit == PERCENT for c in conditions):
        filtered.setdefault("condition_notes", []).append(
            "Organizations without a figure (for a margin: no revenue in the period) cannot meet the condition "
            "and are listed separately.")
    return filtered
