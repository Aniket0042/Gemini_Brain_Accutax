"""watches.py — Things a VAT bot can watch, mapped to review_vat rules.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class Watch:
    id: str
    label: str            # shown on the card
    side: str             # "purchases" or "sales" (review_vat side)
    rules: Tuple[str, ...]
    default_on: bool


_WATCHES: Tuple[Watch, ...] = (
    Watch(
        id="duplicate_bill",
        label="A bill looks recorded twice",
        side="purchases",
        rules=("same_number", "same_total_near_date", "similar_vendor_same_day"),
        default_on=True,
    ),
    Watch(
        id="vat_rate_wrong",
        label="VAT on a bill is not 0% or 5%",
        side="purchases",
        rules=("vat_rate_not_standard", "vat_wrong_sign", "vat_without_amount"),
        default_on=True,
    ),
    Watch(
        id="bill_missing_details",
        label="A bill is missing its supplier, number or has a future date",
        side="purchases",
        rules=("missing_supplier", "missing_number", "future_date"),
        default_on=False,
    ),
    Watch(
        id="unusual_bill",
        label="A bill far above the supplier's usual amount",
        side="purchases",
        rules=("large_for_supplier", "large_one_off_supplier"),
        default_on=False,
    ),
    Watch(
        id="sales_vat_wrong",
        label="A sales invoice with wrong or no output VAT",
        side="sales",
        rules=("vat_rate_not_standard", "vat_zero", "vat_wrong_sign"),
        default_on=True,
    ),
)

_WATCH_MAP: Dict[str, Watch] = {w.id: w for w in _WATCHES}


def all_watches() -> List[Watch]:
    """All watches available for a VAT bot."""
    return list(_WATCHES)


def get(watch_id: str) -> Watch:
    """Get a watch by id. Raises KeyError if unknown."""
    if watch_id not in _WATCH_MAP:
        raise KeyError(f"Unknown watch id: {watch_id!r}")
    return _WATCH_MAP[watch_id]


def default_ids() -> List[str]:
    """The watch ids that are on by default."""
    return [w.id for w in _WATCHES if w.default_on]


def watch_for_rule(rule: str, side: str | None = None) -> Watch | None:
    """Find the watch that monitors this rule (Task 3.3 'Why did I get this?')."""
    if rule in _WATCH_MAP:
        w = _WATCH_MAP[rule]
        if not side or w.side == side:
            return w
    if side:
        for w in _WATCHES:
            if w.side == side and (rule in w.rules or rule == w.id):
                return w
    for w in _WATCHES:
        if rule in w.rules or rule == w.id:
            return w
    return None



def review_calls(watch_ids: Sequence[str], sensitivity: str = "balanced") -> List[Dict[str, Any]]:
    """Build review_vat call parameter dictionaries from selected watches.

    Returns one dict per side ('purchases', 'sales') that has active rules.
    """
    purchases_rules: set[str] = set()
    sales_rules: set[str] = set()

    for wid in watch_ids:
        w = get(wid)
        if w.side == "purchases":
            purchases_rules.update(w.rules)
        elif w.side == "sales":
            sales_rules.update(w.rules)

    # Sensitivity adjustments
    if sensitivity == "fewer":
        purchases_rules.discard("similar_vendor_same_day")
        purchases_rules.discard("large_one_off_supplier")
    elif sensitivity == "more":
        if purchases_rules:
            purchases_rules.add("round_amount")

    calls: List[Dict[str, Any]] = []
    if purchases_rules:
        calls.append({
            "mode": "all",
            "side": "purchases",
            "rules": sorted(purchases_rules),
        })
    if sales_rules:
        calls.append({
            "mode": "all",
            "side": "sales",
            "rules": sorted(sales_rules),
        })

    return calls
