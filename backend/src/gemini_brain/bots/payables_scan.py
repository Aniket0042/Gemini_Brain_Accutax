"""payables_scan.py — What the AP bot reads that review_vat does not: open bills that are overdue or due soon, large
first bills from new suppliers, and the payables position (docs/BOT_AGENTS_AND_AP_PLAN.md, task B3).

Read only. Every query goes through cube_client.load with the organizations already authorized. Figures come from
Cube; the sentences are written here from those figures.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic import cube_client
from gemini_brain.semantic.query_metrics import cell_text
from gemini_brain.vat_review.scan import scan_bills

#: Cube returns at most this many rows for one query.
CUBE_ROW_LIMIT = 500
#: Vendors per "had earlier bills?" query, so one query stays well under the row limit.
VENDOR_CHUNK = 100
_EPOCH = "2000-01-01"


def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def bill_key(org_id: int, document_id: Any) -> str:
    """One key per bill, without the rule: a bill that was due soon and is now overdue stays the same finding."""
    return _key("ap", "bill", org_id, document_id)


def supplier_key(org_id: int, vendor_name: str) -> str:
    """One key per new supplier, however many bills it sent."""
    return _key("ap", "new_supplier", org_id, (vendor_name or "").strip().lower())


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def _date(v: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(cell_text(v))[:10]) if v not in (None, "") else None
    except ValueError:
        return None


def money(amount: Optional[float], currency: Optional[str]) -> str:
    return f"{currency or 'AED'} {amount or 0:,.2f}"


def _day(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _plain(raw: Dict[str, Any], view: str) -> Dict[str, Any]:
    """A Cube row with the view's prefix taken off and Decimals as text."""
    return {k.split(".", 1)[1] if k.startswith(view + ".") else k: cell_text(v) for k, v in raw.items()}


# --- overdue and due soon -----------------------------------------------------------------------------------------

def open_bills(org_id: int, today: dt.date, due_by: dt.date, *, subject: str, deadline: float) -> Tuple[List[Dict[str, Any]], bool]:
    """Open bills of one organization due on or before `due_by`, earliest due first. Returns (rows, complete): a scan
    that hits the row limit keeps the earliest-due bills (the overdue ones) and says it is not complete."""
    query = {
        "dimensions": [f"payables.{d}" for d in ("document_id", "document_number", "vendor_name", "document_date",
                                                  "due_date", "days_overdue", "currency", "organization_name")],
        "measures": ["payables.outstanding"],
        "filters": [{"member": "payables.due_date", "operator": "beforeDate",
                     "values": [(due_by + dt.timedelta(days=1)).isoformat()]}],
        "order": {"payables.due_date": "asc"},
        "limit": CUBE_ROW_LIMIT,
    }
    result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
    rows = [_plain(r, "payables") for r in result.rows]
    return rows, len(rows) < CUBE_ROW_LIMIT


def due_items(org_id: int, today: dt.date, *, overdue_days: int, due_soon_days: int, rules: Sequence[str],
              subject: str, deadline: float) -> Tuple[List[Dict[str, Any]], int, bool]:
    """One finding per open bill that is at least `overdue_days` overdue (bill_overdue) or due within `due_soon_days`
    (bill_due_soon). Returns (items, open bills read, complete)."""
    rows, complete = open_bills(org_id, today, today + dt.timedelta(days=due_soon_days), subject=subject,
                                deadline=deadline)
    items: List[Dict[str, Any]] = []
    for r in rows:
        due = _date(r.get("due_date"))
        outstanding = _num(r.get("outstanding"))
        if due is None or not outstanding:
            continue
        days_over = (today - due).days
        currency = r.get("currency")
        vendor = r.get("vendor_name") or "A supplier"
        number = r.get("document_number") or r.get("document_id")
        if days_over >= max(1, overdue_days):
            rule = "bill_overdue"
            severity = "high" if days_over >= 30 else "medium"
            why = (f"{vendor} bill {number} for {money(outstanding, currency)} was due on {_day(due)} and is "
                   f"{days_over} day{'s' if days_over != 1 else ''} overdue.")
        elif 0 <= -days_over <= due_soon_days:
            rule = "bill_due_soon"
            severity = "low"
            left = -days_over
            when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
            why = f"{vendor} bill {number} for {money(outstanding, currency)} is due {when}, on {_day(due)}."
        else:
            continue  # overdue, but fewer days than this sensitivity flags
        if rule not in rules:
            continue
        items.append({
            "key": bill_key(org_id, r.get("document_id")), "rule": rule, "organization_id": org_id,
            "organization_name": r.get("organization_name"), "currency": currency, "severity": severity, "why": why,
            "bill": {"document_id": r.get("document_id"), "document_number": r.get("document_number"),
                     "document_date": str(r.get("document_date") or "")[:10], "vendor_name": r.get("vendor_name"),
                     "net_purchases": None, "input_vat": None, "due_date": due.isoformat(),
                     "outstanding": outstanding, "days_overdue": max(days_over, 0), "total": outstanding},
        })
    return items, len(rows), complete


# --- new suppliers ------------------------------------------------------------------------------------------------

def _vendors_with_earlier_bills(org_id: int, vendors: Sequence[str], before: dt.date, *, subject: str,
                                deadline: float) -> set:
    """Which of these suppliers have a bill dated before `before`."""
    found: set = set()
    for i in range(0, len(vendors), VENDOR_CHUNK):
        chunk = list(vendors[i:i + VENDOR_CHUNK])
        query = {
            "dimensions": ["purchases.vendor_name"],
            "measures": ["purchases.bill_count"],
            "filters": [{"member": "purchases.vendor_name", "operator": "equals", "values": chunk}],
            "timeDimensions": [{"dimension": "purchases.document_date",
                                "dateRange": [_EPOCH, (before - dt.timedelta(days=1)).isoformat()]}],
            "limit": CUBE_ROW_LIMIT,
        }
        result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
        for r in result.rows:
            row = _plain(r, "purchases")
            if (_num(row.get("bill_count")) or 0) > 0 and row.get("vendor_name"):
                found.add(row["vendor_name"])
    return found


def new_supplier_items(org_id: int, start: dt.date, end: dt.date, *, min_amount: float, subject: str,
                       deadline: float) -> Tuple[List[Dict[str, Any]], int, bool]:
    """One finding per supplier with no bill before `start` whose first bill in the period is at least `min_amount`
    (amount plus VAT). Returns (items, bills read, complete). A supplier entered under a different name looks new."""
    scan = scan_bills(org_id, start, end, subject=subject, deadline=deadline)
    first: Dict[str, Dict[str, Any]] = {}
    for b in sorted(scan.bills, key=lambda b: (b.get("document_date") or "", str(b.get("document_id") or ""))):
        vendor = (b.get("vendor_name") or "").strip()
        if vendor and vendor not in first:
            first[vendor] = b
    if not first:
        return [], len(scan.bills), scan.complete
    known = _vendors_with_earlier_bills(org_id, sorted(first), start, subject=subject, deadline=deadline)
    items: List[Dict[str, Any]] = []
    for vendor, b in first.items():
        if vendor in known:
            continue
        total = (b.get("net_purchases") or 0.0) + (b.get("input_vat") or 0.0)
        if total < min_amount:
            continue
        currency = b.get("currency")
        date = _date(b.get("document_date"))
        why = (f"{vendor} is a new supplier: its first bill, {b.get('document_number') or b.get('document_id')} "
               f"on {_day(date) if date else 'an unknown date'}, is {money(total, currency)}, and it has no earlier bills.")
        items.append({
            "key": supplier_key(org_id, vendor), "rule": "new_supplier_large", "organization_id": org_id,
            "organization_name": b.get("organization_name"), "currency": currency,
            "severity": "medium" if total >= 4 * min_amount else "low", "why": why,
            "bill": {"document_id": b.get("document_id"), "document_number": b.get("document_number"),
                     "document_date": b.get("document_date"), "vendor_name": vendor,
                     "net_purchases": b.get("net_purchases"), "input_vat": b.get("input_vat"), "total": round(total, 2)},
        })
    return items, len(scan.bills), scan.complete


# --- position -----------------------------------------------------------------------------------------------------

def position(org_ids: Sequence[int], today: dt.date, *, subject: str, deadline: float) -> Optional[Dict[str, Any]]:
    """What is overdue now and what falls due in the next 7 days, from the payables view. Amounts are added up only
    over organizations that share the first one's currency; `organizations` says how many the figures cover."""
    if not org_ids:
        return None
    overdue = cube_client.load({"dimensions": ["payables.currency"],
                                "measures": ["payables.overdue_amount", "payables.overdue_count"]},
                               organization_ids=list(org_ids), subject=subject, deadline=deadline)
    soon = cube_client.load({"dimensions": ["payables.currency"],
                             "measures": ["payables.outstanding", "payables.open_count"],
                             "filters": [{"member": "payables.due_date", "operator": "inDateRange",
                                          "values": [today.isoformat(), (today + dt.timedelta(days=7)).isoformat()]}]},
                            organization_ids=list(org_ids), subject=subject, deadline=deadline)
    # Open bills with no due date can be neither overdue nor due soon; the box says how many there are.
    undated = cube_client.load({"dimensions": ["payables.currency"],
                                "measures": ["payables.outstanding", "payables.open_count"],
                                "filters": [{"member": "payables.due_date", "operator": "notSet"}]},
                               organization_ids=list(org_ids), subject=subject, deadline=deadline)
    rows_undated = [_plain(r, "payables") for r in undated.rows]
    rows_over = [_plain(r, "payables") for r in overdue.rows]
    rows_soon = [_plain(r, "payables") for r in soon.rows]
    currency = next((r.get("currency") for r in rows_over + rows_soon if r.get("currency")), None) or "AED"
    same = [r for r in rows_over if (r.get("currency") or currency) == currency]
    same_soon = [r for r in rows_soon if (r.get("currency") or currency) == currency]
    same_undated = [r for r in rows_undated if (r.get("currency") or currency) == currency]
    covered = {r.get("organization_id") for r in same + same_soon if r.get("organization_id") is not None}
    return {
        "kind": "payables", "currency": currency,
        "overdue": round(sum(_num(r.get("overdue_amount")) or 0.0 for r in same), 2),
        "overdue_count": int(sum(_num(r.get("overdue_count")) or 0 for r in same)),
        "due_next_7_days": round(sum(_num(r.get("outstanding")) or 0.0 for r in same_soon), 2),
        "due_next_7_days_count": int(sum(_num(r.get("open_count")) or 0 for r in same_soon)),
        "no_due_date": round(sum(_num(r.get("outstanding")) or 0.0 for r in same_undated), 2),
        "no_due_date_count": int(sum(_num(r.get("open_count")) or 0 for r in same_undated)),
        "organizations": len(covered) or len(org_ids),
        "other_currencies": len({r.get("currency") for r in rows_over + rows_soon} - {currency, None}) > 0,
        "as_of": today.isoformat(),
    }
