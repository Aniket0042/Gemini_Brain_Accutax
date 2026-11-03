"""ap_engine.py — The AP bot's engine: review_vat for duplicates, missing details and unusual bills, the payables
view for overdue and due-soon bills, and the purchases view for large bills from new suppliers
(docs/BOT_AGENTS_AND_AP_PLAN.md, task B3).

Every call's result has review_vat's shape (side, period, bills_checked, organizations, groups, findings, *_found,
*_by_rule), so preview, run and the monitor treat it like a VAT review.
"""
from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from typing import AbstractSet, Any, Dict, List, Optional, Sequence

from gemini_brain.bots import payables_scan
from gemini_brain.config.settings import settings
from gemini_brain.semantic import periods as report_periods
from gemini_brain.vat_review import tool as vat_review_tool

#: Thresholds per sensitivity. Proposed, not yet signed off by finance (docs/BOT_AGENTS_AND_AP_PLAN.md, B1).
OVERDUE_DAYS = {"fewer": 14, "balanced": 1, "more": 1}
DUE_SOON_DAYS = {"fewer": 2, "balanced": 3, "more": 7}
NEW_SUPPLIER_MIN = {"fewer": 20_000.0, "balanced": 5_000.0, "more": 1_000.0}
#: The window a scheduled AP run reviews and looks for new suppliers in.
LOOKBACK_DAYS = 90
#: Seconds one organization's scan may take, inside the run's deadline.
SCAN_SECONDS = 45.0

REVIEW_RULES = {
    "duplicate_bill": ("same_number", "same_total_near_date", "similar_vendor_same_day"),
    "unusual_bill": ("large_for_supplier", "large_one_off_supplier"),
    "bill_missing_details": ("missing_supplier", "missing_number", "future_date"),
}
DUPLICATE_RULES = frozenset(REVIEW_RULES["duplicate_bill"])


def _today() -> dt.date:
    return report_periods.today_in(settings.report_timezone)


def _num(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve(period: Optional[Dict[str, str]]) -> tuple[dt.date, dt.date]:
    today = _today()
    if not period:
        return today - dt.timedelta(days=LOOKBACK_DAYS - 1), today
    return report_periods.resolve(period, today)


def _result(items: List[Dict[str, Any]], *, start: dt.date, end: dt.date, checked: int,
            per_org: List[Dict[str, Any]], exclude: AbstractSet[str]) -> Dict[str, Any]:
    kept = [i for i in items if i["key"] not in exclude]
    return {
        "side": "purchases", "period": {"start": start.isoformat(), "end": end.isoformat()},
        "bills_checked": checked, "organizations": per_org, "groups": [], "groups_found": 0, "groups_by_rule": {},
        "findings": kept, "findings_found": len(kept), "findings_by_rule": dict(Counter(i["rule"] for i in kept)),
        "dismissed": len(items) - len(kept),
    }


class ApEngine:
    """review_vat, the payables view and the purchases view, behind one engine."""

    def calls(self, watch_ids: Sequence[str], sensitivity: str = "balanced") -> List[Dict[str, Any]]:
        known = set(REVIEW_RULES) | {"bill_overdue", "bill_due_soon", "new_supplier"}
        for wid in watch_ids:
            if wid not in known:
                raise KeyError(f"Unknown watch id: {wid!r}")
        sens = sensitivity if sensitivity in OVERDUE_DAYS else "balanced"
        review: set[str] = set()
        for wid, rules in REVIEW_RULES.items():
            if wid in watch_ids:
                review.update(rules)
        if sens == "fewer":
            review.discard("similar_vendor_same_day")
            review.discard("large_one_off_supplier")
        elif sens == "more" and "unusual_bill" in watch_ids:
            review.add("round_amount")

        calls: List[Dict[str, Any]] = []
        if review:
            calls.append({"kind": "review", "side": "purchases", "mode": "all", "rules": sorted(review)})
        due_rules = [r for r in ("bill_overdue", "bill_due_soon") if r in watch_ids]
        if due_rules:
            calls.append({"kind": "payables", "side": "purchases", "rules": due_rules,
                          "overdue_days": OVERDUE_DAYS[sens], "due_soon_days": DUE_SOON_DAYS[sens]})
        if "new_supplier" in watch_ids:
            calls.append({"kind": "new_supplier", "side": "purchases", "min_amount": NEW_SUPPLIER_MIN[sens]})
        return calls

    def run(self, call: Dict[str, Any], *, org_ids: Sequence[int], period: Optional[Dict[str, str]], subject: str,
            deadline: float, exclude: AbstractSet[str] = frozenset(), unlimited: bool = False) -> Dict[str, Any]:
        kind = call.get("kind")
        start, end = _resolve(period)
        if kind == "review":
            params = {k: v for k, v in call.items() if k != "kind"}
            return vat_review_tool.run({**params, "period": {"start": start.isoformat(), "end": end.isoformat()},
                                        "limit": 50}, organization_ids=list(org_ids), subject=subject,
                                       deadline=deadline, exclude=exclude, unlimited=unlimited)

        items: List[Dict[str, Any]] = []
        per_org: List[Dict[str, Any]] = []
        checked = 0
        for org in org_ids:
            org_deadline = min(deadline, time.monotonic() + SCAN_SECONDS)
            if kind == "payables":
                found, read, complete = payables_scan.due_items(
                    org, _today(), overdue_days=int(call["overdue_days"]), due_soon_days=int(call["due_soon_days"]),
                    rules=call["rules"], subject=subject, deadline=org_deadline)
            elif kind == "new_supplier":
                found, read, complete = payables_scan.new_supplier_items(
                    org, start, end, min_amount=float(call["min_amount"]), subject=subject, deadline=org_deadline)
            else:
                raise ValueError(f"Unknown AP call kind: {kind!r}")
            items += found
            checked += read
            per_org.append({"organization_id": org, "currency": next((i["currency"] for i in found if i.get("currency")), None),
                            "bills_checked": read, "complete": complete})
        if kind == "payables":  # payables are as of today, whatever the period
            start = end = _today()
        return _result(items, start=start, end=end, checked=checked, per_org=per_org, exclude=exclude)

    def period(self, draft: Any, subject: str) -> Optional[Dict[str, str]]:
        """The last 90 days, for every AP schedule."""
        start, end = _resolve(None)
        return {"start": start.isoformat(), "end": end.isoformat()}

    def position(self, org_ids: Sequence[int], subject: str, deadline: float) -> Any:
        try:
            return payables_scan.position(org_ids, _today(), subject=subject, deadline=deadline)
        except Exception:
            return None

    def summarize_position(self, raw: Any, reviews: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return raw if isinstance(raw, dict) and raw.get("kind") == "payables" else None

    def stake(self, rule: str, detail: Dict[str, Any]) -> float:
        """Duplicates: the amount plus VAT of every copy after the first (could be paid twice). Other rules: 0; what
        is overdue or due soon shows in the position box."""
        if rule not in DUPLICATE_RULES:
            return 0.0
        return round(sum(abs(_num(b.get("amount")) + _num(b.get("vat"))) for b in (detail.get("bills") or [])[1:]), 2)

    def detail(self, item: Dict[str, Any], side: str) -> Dict[str, Any]:
        from gemini_brain.bots.agents_vat import finding_detail
        out = finding_detail(item, side)
        for b in out["bills"]:
            if b.get("total") is None and (b.get("amount") is not None or b.get("vat") is not None):
                b["total"] = round(_num(b.get("amount")) + _num(b.get("vat")), 2)
        return out
