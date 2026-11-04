"""ar_engine.py — The AR bot's engine: review_vat on sales invoices for duplicates, missing details and unusual
invoices, the receivables view for invoices that just went overdue or are due soon and for customers with old debt,
and the sales view for large first invoices to new customers (docs/BOT_AR_PLAN.md, task C4).

Every call's result has review_vat's shape, so preview, run and the monitor treat it like a VAT review.
"""
from __future__ import annotations

import time
from typing import AbstractSet, Any, Dict, List, Optional, Sequence

from gemini_brain.bots import open_docs_scan
from gemini_brain.bots.ap_engine import LOOKBACK_DAYS, SCAN_SECONDS, _num, _resolve, _result, _today  # noqa: F401
from gemini_brain.vat_review import tool as vat_review_tool

#: Thresholds per sensitivity. Proposed, not yet signed off by finance (docs/BOT_AR_PLAN.md, C1).
OVERDUE_DAYS = {"fewer": 7, "balanced": 1, "more": 1}
DUE_SOON_DAYS = {"fewer": 2, "balanced": 3, "more": 7}
CUSTOMER_OVERDUE_MIN = {"fewer": 50_000.0, "balanced": 10_000.0, "more": 2_000.0}
NEW_CUSTOMER_MIN = {"fewer": 50_000.0, "balanced": 20_000.0, "more": 5_000.0}
#: Invoices are findings of their own only for this many days overdue; older debt is one finding per customer.
RECENT_OVERDUE_DAYS = 30

REVIEW_RULES = {
    "duplicate_invoice": ("same_number", "same_total_near_date", "similar_vendor_same_day"),
    "unusual_invoice": ("large_for_supplier", "large_one_off_supplier"),
    "invoice_missing_details": ("missing_supplier", "missing_number", "future_date"),
}
DUPLICATE_RULES = frozenset(REVIEW_RULES["duplicate_invoice"])
#: Rules whose finding is one open invoice: gone from the scan does not mean paid (it may have aged out).
INVOICE_RULES = frozenset({"invoice_overdue", "invoice_due_soon"})


class ArEngine:
    """review_vat on sales, the receivables view and the sales view, behind one engine."""

    def calls(self, watch_ids: Sequence[str], sensitivity: str = "balanced") -> List[Dict[str, Any]]:
        known = set(REVIEW_RULES) | {"invoice_overdue", "invoice_due_soon", "customer_overdue", "new_customer"}
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
        elif sens == "more" and "unusual_invoice" in watch_ids:
            review.add("round_amount")

        calls: List[Dict[str, Any]] = []
        if review:
            calls.append({"kind": "review", "side": "sales", "mode": "all", "rules": sorted(review)})
        due_rules = [r for r in ("invoice_overdue", "invoice_due_soon") if r in watch_ids]
        if due_rules:
            calls.append({"kind": "receivables", "side": "sales", "rules": due_rules,
                          "overdue_days": OVERDUE_DAYS[sens], "due_soon_days": DUE_SOON_DAYS[sens]})
        if "customer_overdue" in watch_ids:
            calls.append({"kind": "customers", "side": "sales", "min_amount": CUSTOMER_OVERDUE_MIN[sens]})
        if "new_customer" in watch_ids:
            calls.append({"kind": "new_customer", "side": "sales", "min_amount": NEW_CUSTOMER_MIN[sens]})
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
            if kind == "receivables":
                found, read, complete = open_docs_scan.due_items(
                    "receivables", org, _today(), overdue_days=int(call["overdue_days"]),
                    due_soon_days=int(call["due_soon_days"]), rules=call["rules"], subject=subject,
                    deadline=org_deadline, max_overdue_days=RECENT_OVERDUE_DAYS)
            elif kind == "customers":
                found, read, complete = open_docs_scan.customer_items(
                    org, min_amount=float(call["min_amount"]), subject=subject, deadline=org_deadline)
            elif kind == "new_customer":
                found, read, complete = open_docs_scan.new_party_items(
                    "receivables", org, start, end, min_amount=float(call["min_amount"]), subject=subject,
                    deadline=org_deadline)
            else:
                raise ValueError(f"Unknown AR call kind: {kind!r}")
            items += found
            checked += read
            per_org.append({"organization_id": org, "currency": next((i["currency"] for i in found if i.get("currency")), None),
                            "bills_checked": read, "complete": complete})
        if kind in ("receivables", "customers"):  # receivables are as of today, whatever the period
            start = end = _today()
        return _result(items, start=start, end=end, checked=checked, per_org=per_org, exclude=exclude, side="sales")

    def period(self, draft: Any, subject: str) -> Optional[Dict[str, str]]:
        """The last 90 days, for every AR schedule."""
        start, end = _resolve(None)
        return {"start": start.isoformat(), "end": end.isoformat()}

    def position(self, org_ids: Sequence[int], subject: str, deadline: float) -> Any:
        try:
            return open_docs_scan.position("receivables", org_ids, _today(), subject=subject, deadline=deadline)
        except Exception:
            return None

    def summarize_position(self, raw: Any, reviews: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return raw if isinstance(raw, dict) and raw.get("kind") == "receivables" else None

    def stake(self, rule: str, detail: Dict[str, Any]) -> float:
        """Duplicates: the amount plus VAT of every copy after the first (could be billed twice). Other rules: 0; the
        overdue amount comes from the receivables position, so customer and invoice findings are not counted twice."""
        if rule not in DUPLICATE_RULES:
            return 0.0
        return round(sum(abs(_num(b.get("amount")) + _num(b.get("vat"))) for b in (detail.get("bills") or [])[1:]), 2)

    def detail(self, item: Dict[str, Any], side: str) -> Dict[str, Any]:
        from gemini_brain.bots.agents_vat import finding_detail
        out = finding_detail(item, side)
        for b in out["bills"]:
            if b.get("total") is None and (b.get("amount") is not None or b.get("vat") is not None):
                b["total"] = round(_num(b.get("amount")) + _num(b.get("vat")), 2)
        if item.get("buckets"):  # a customer with old debt: the aging table instead of invoices
            out["buckets"] = item["buckets"]
            out["party"] = (item.get("bill") or {}).get("vendor_name")
            out["overdue"] = (item.get("bill") or {}).get("total")
            out["bills"] = []
        return out

    def confirm_fixed(self, findings: Sequence[Dict[str, Any]], *, org_ids: Sequence[int], subject: str,
                      deadline: float) -> set:
        """Of these findings that the run no longer found, the keys that are really fixed. An invoice that aged past
        RECENT_OVERDUE_DAYS leaves the scan but is still open, so it is not fixed. Every other rule is fixed when not
        found. If the check fails, nothing is marked fixed this run."""
        fixed = {f["key"] for f in findings if f["rule"] not in INVOICE_RULES}
        by_org: Dict[int, List[Dict[str, Any]]] = {}
        for f in findings:
            if f["rule"] in INVOICE_RULES:
                by_org.setdefault(int(f.get("org_id") or org_ids[0]), []).append(f)
        try:
            for org, fs in by_org.items():
                ids = {f["key"]: ((f.get("detail") or {}).get("bills") or [{}])[0].get("id") for f in fs}
                open_ids = open_docs_scan.still_open("receivables", org, [i for i in ids.values() if i is not None],
                                                     subject=subject, deadline=deadline)
                fixed.update(k for k, doc in ids.items() if doc is None or str(doc) not in open_ids)
        except Exception:
            return {f["key"] for f in findings if f["rule"] not in INVOICE_RULES}
        return fixed
