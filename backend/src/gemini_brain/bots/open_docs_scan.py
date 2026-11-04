"""open_docs_scan.py — What the AP and AR bots read that review_vat does not: open documents that are overdue or due
soon, large first documents from new parties, customers with old overdue debt, and the payables or receivables
position (docs/BOT_AR_PLAN.md, task C3; moved here from bots/payables_scan.py, which keeps the AP names).

Read only. Every query goes through cube_client.load with the organizations already authorized. Figures come from
Cube; the sentences are written here from those figures.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic import cube_client
from gemini_brain.semantic.query_metrics import cell_text
from gemini_brain.vat_review.scan import scan_bills

#: Cube returns at most this many rows for one query.
CUBE_ROW_LIMIT = 500
#: Parties per "had earlier documents?" query, so one query stays well under the row limit.
VENDOR_CHUNK = 100
_EPOCH = "2000-01-01"
#: The receivables aging buckets that are overdue, oldest last.
OVERDUE_BUCKETS = ("1-30 days", "31-60 days", "61-90 days", "Over 90 days")

#: Per side: the open-documents view and its party column, the flow view new parties are looked up in, the review
#: side, the key prefix, the rule names and the words for the sentences.
SIDES: Dict[str, Dict[str, Any]] = {
    "payables": {
        "view": "payables", "party": "vendor_name", "flow": "purchases", "flow_party": "vendor_name",
        "count": "bill_count", "review_side": "purchases", "prefix": "ap", "doc_key": "bill",
        "party_key": "new_supplier", "overdue_rule": "bill_overdue", "due_soon_rule": "bill_due_soon",
        "new_rule": "new_supplier_large", "doc": "bill", "party_word": "supplier", "no_party": "A supplier",
        "high_days": 30,
    },
    "receivables": {
        "view": "receivables", "party": "customer_name", "flow": "sales", "flow_party": "customer_name",
        "count": "invoice_count", "review_side": "sales", "prefix": "ar", "doc_key": "invoice",
        "party_key": "new_customer", "overdue_rule": "invoice_overdue", "due_soon_rule": "invoice_due_soon",
        "new_rule": "new_customer_large", "doc": "invoice", "party_word": "customer", "no_party": "A customer",
        "high_days": 15,
    },
}


def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def doc_key(side: str, org_id: int, document_id: Any) -> str:
    """One key per document, without the rule: a document that was due soon and is now overdue stays the same finding."""
    spec = SIDES[side]
    return _key(spec["prefix"], spec["doc_key"], org_id, document_id)


def party_key(side: str, org_id: int, name: str) -> str:
    """One key per new supplier or customer, however many documents it has."""
    spec = SIDES[side]
    return _key(spec["prefix"], spec["party_key"], org_id, (name or "").strip().lower())


def customer_key(org_id: int, name: str) -> str:
    """One key per customer with old overdue debt."""
    return _key("ar", "customer", org_id, (name or "").strip().lower())


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

def open_docs(side: str, org_id: int, today: dt.date, due_by: dt.date, *, due_from: Optional[dt.date] = None,
              subject: str, deadline: float) -> Tuple[List[Dict[str, Any]], bool]:
    """Open documents of one organization due on or before `due_by` (and on or after `due_from`, when given). Returns
    (rows, complete). Without `due_from` the earliest due come first, so a scan that hits the row limit keeps the most
    overdue; with it the latest due come first, so it keeps the most recent."""
    view = SIDES[side]["view"]
    party = SIDES[side]["party"]
    if due_from is None:
        flt = {"member": f"{view}.due_date", "operator": "beforeDate",
               "values": [(due_by + dt.timedelta(days=1)).isoformat()]}
    else:
        flt = {"member": f"{view}.due_date", "operator": "inDateRange",
               "values": [due_from.isoformat(), due_by.isoformat()]}
    query = {
        "dimensions": [f"{view}.{d}" for d in ("document_id", "document_number", party, "document_date",
                                               "due_date", "days_overdue", "currency", "organization_name")],
        "measures": [f"{view}.outstanding"],
        "filters": [flt],
        "order": {f"{view}.due_date": "asc" if due_from is None else "desc"},
        "limit": CUBE_ROW_LIMIT,
    }
    result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
    rows = [_plain(r, view) for r in result.rows]
    return rows, len(rows) < CUBE_ROW_LIMIT


def _due_sentence(side: str, rule: str, party: str, number: Any, amount: str, due: dt.date, days_over: int) -> str:
    if side == "payables":
        if rule == "bill_overdue":
            return (f"{party} bill {number} for {amount} was due on {_day(due)} and is "
                    f"{days_over} day{'s' if days_over != 1 else ''} overdue.")
        left = -days_over
        when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
        return f"{party} bill {number} for {amount} is due {when}, on {_day(due)}."
    if rule == "invoice_overdue":
        return (f"Invoice {number} to {party} for {amount} was due on {_day(due)} and is "
                f"{days_over} day{'s' if days_over != 1 else ''} overdue.")
    left = -days_over
    when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
    return f"Invoice {number} to {party} for {amount} is due {when}, on {_day(due)}."


def due_items(side: str, org_id: int, today: dt.date, *, overdue_days: int, due_soon_days: int, rules: Sequence[str],
              subject: str, deadline: float, max_overdue_days: Optional[int] = None
              ) -> Tuple[List[Dict[str, Any]], int, bool]:
    """One finding per open document that is at least `overdue_days` overdue (and at most `max_overdue_days`, when
    given) or due within `due_soon_days`. Returns (items, open documents read, complete)."""
    spec = SIDES[side]
    due_by = today + dt.timedelta(days=due_soon_days)
    due_from = today - dt.timedelta(days=max_overdue_days) if max_overdue_days is not None else None
    rows, complete = open_docs(side, org_id, today, due_by, due_from=due_from, subject=subject, deadline=deadline)
    items: List[Dict[str, Any]] = []
    for r in rows:
        due = _date(r.get("due_date"))
        outstanding = _num(r.get("outstanding"))
        if due is None or not outstanding:
            continue
        days_over = (today - due).days
        if days_over >= max(1, overdue_days):
            rule = spec["overdue_rule"]
            severity = "high" if days_over >= spec["high_days"] else "medium"
        elif 0 <= -days_over <= due_soon_days:
            rule = spec["due_soon_rule"]
            severity = "low"
        else:
            continue  # overdue, but fewer days than this sensitivity flags
        if rule not in rules:
            continue
        currency = r.get("currency")
        party = r.get(spec["party"]) or spec["no_party"]
        number = r.get("document_number") or r.get("document_id")
        items.append({
            "key": doc_key(side, org_id, r.get("document_id")), "rule": rule, "organization_id": org_id,
            "organization_name": r.get("organization_name"), "currency": currency, "severity": severity,
            "why": _due_sentence(side, rule, party, number, money(outstanding, currency), due, days_over),
            "bill": {"document_id": r.get("document_id"), "document_number": r.get("document_number"),
                     "document_date": str(r.get("document_date") or "")[:10], "vendor_name": r.get(spec["party"]),
                     "net_purchases": None, "input_vat": None, "due_date": due.isoformat(),
                     "outstanding": outstanding, "days_overdue": max(days_over, 0), "total": outstanding},
        })
    return items, len(rows), complete


def still_open(side: str, org_id: int, document_ids: Sequence[Any], *, subject: str, deadline: float) -> set:
    """Which of these documents are still open, as strings."""
    view = SIDES[side]["view"]
    ids = [str(d) for d in document_ids if d not in (None, "")]
    found: set = set()
    for i in range(0, len(ids), VENDOR_CHUNK):
        query = {"dimensions": [f"{view}.document_id"],
                 "filters": [{"member": f"{view}.document_id", "operator": "equals", "values": ids[i:i + VENDOR_CHUNK]}],
                 "limit": CUBE_ROW_LIMIT}
        result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
        found.update(str(_plain(r, view).get("document_id")) for r in result.rows)
    return found


# --- new suppliers and customers ----------------------------------------------------------------------------------

def _parties_with_earlier_docs(side: str, org_id: int, names: Sequence[str], before: dt.date, *, subject: str,
                               deadline: float) -> set:
    """Which of these suppliers or customers have a document dated before `before`."""
    spec = SIDES[side]
    flow, member, count = spec["flow"], f"{spec['flow']}.{spec['flow_party']}", f"{spec['flow']}.{spec['count']}"
    found: set = set()
    for i in range(0, len(names), VENDOR_CHUNK):
        chunk = list(names[i:i + VENDOR_CHUNK])
        query = {
            "dimensions": [member],
            "measures": [count],
            "filters": [{"member": member, "operator": "equals", "values": chunk}],
            "timeDimensions": [{"dimension": f"{flow}.document_date",
                                "dateRange": [_EPOCH, (before - dt.timedelta(days=1)).isoformat()]}],
            "limit": CUBE_ROW_LIMIT,
        }
        result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
        for r in result.rows:
            row = _plain(r, flow)
            if (_num(row.get(spec["count"])) or 0) > 0 and row.get(spec["flow_party"]):
                found.add(row[spec["flow_party"]])
    return found


def new_party_items(side: str, org_id: int, start: dt.date, end: dt.date, *, min_amount: float, subject: str,
                    deadline: float) -> Tuple[List[Dict[str, Any]], int, bool]:
    """One finding per supplier or customer with no document before `start` whose first document in the period is at
    least `min_amount` (amount plus VAT). Returns (items, documents read, complete). A party entered under a different
    name looks new."""
    spec = SIDES[side]
    scan = scan_bills(org_id, start, end, subject=subject, deadline=deadline, side=spec["review_side"])
    first: Dict[str, Dict[str, Any]] = {}
    for b in sorted(scan.bills, key=lambda b: (b.get("document_date") or "", str(b.get("document_id") or ""))):
        name = (b.get("vendor_name") or "").strip()  # the customer, on the sales side
        if name and name not in first:
            first[name] = b
    if not first:
        return [], len(scan.bills), scan.complete
    known = _parties_with_earlier_docs(side, org_id, sorted(first), start, subject=subject, deadline=deadline)
    items: List[Dict[str, Any]] = []
    for name, b in first.items():
        if name in known:
            continue
        total = (b.get("net_purchases") or 0.0) + (b.get("input_vat") or 0.0)
        if total < min_amount:
            continue
        currency = b.get("currency")
        date = _date(b.get("document_date"))
        why = (f"{name} is a new {spec['party_word']}: its first {spec['doc']}, "
               f"{b.get('document_number') or b.get('document_id')} on {_day(date) if date else 'an unknown date'}, "
               f"is {money(total, currency)}, and it has no earlier {spec['doc']}s.")
        items.append({
            "key": party_key(side, org_id, name), "rule": spec["new_rule"], "organization_id": org_id,
            "organization_name": b.get("organization_name"), "currency": currency,
            "severity": "medium" if total >= 4 * min_amount else "low", "why": why,
            "bill": {"document_id": b.get("document_id"), "document_number": b.get("document_number"),
                     "document_date": b.get("document_date"), "vendor_name": name,
                     "net_purchases": b.get("net_purchases"), "input_vat": b.get("input_vat"), "total": round(total, 2)},
        })
    return items, len(scan.bills), scan.complete


# --- customers with old debt (AR) ---------------------------------------------------------------------------------

def customer_items(org_id: int, *, min_amount: float, subject: str, deadline: float
                   ) -> Tuple[List[Dict[str, Any]], int, bool]:
    """One finding per customer whose overdue amount is at least `min_amount`, with the amount per aging bucket.
    Returns (items, overdue invoices read, complete). One aggregated query: it never lists invoices."""
    query = {
        "dimensions": ["receivables.customer_name", "receivables.aging_bucket", "receivables.currency",
                       "receivables.organization_name"],
        "measures": ["receivables.outstanding", "receivables.open_count"],
        "filters": [{"member": "receivables.aging_bucket", "operator": "equals", "values": list(OVERDUE_BUCKETS)}],
        "order": {"receivables.outstanding": "desc"},
        "limit": CUBE_ROW_LIMIT,
    }
    result = cube_client.load(query, organization_ids=[org_id], subject=subject, deadline=deadline)
    rows = [_plain(r, "receivables") for r in result.rows]
    by_customer: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"buckets": {}, "currency": None, "org": None})
    for r in rows:
        name = r.get("customer_name") or "Unknown"
        c = by_customer[name]
        c["currency"] = c["currency"] or r.get("currency")
        c["org"] = c["org"] or r.get("organization_name")
        c["buckets"][r.get("aging_bucket")] = {"amount": _num(r.get("outstanding")) or 0.0,
                                               "count": int(_num(r.get("open_count")) or 0)}
    items: List[Dict[str, Any]] = []
    read = 0
    for name, c in by_customer.items():
        buckets = [{"bucket": b, **c["buckets"][b]} for b in OVERDUE_BUCKETS if b in c["buckets"]]
        overdue = round(sum(b["amount"] for b in buckets), 2)
        count = sum(b["count"] for b in buckets)
        read += count
        if overdue < min_amount:
            continue
        oldest = buckets[-1]["bucket"]
        severity = "high" if oldest == "Over 90 days" else "medium" if oldest != "1-30 days" else "low"
        old = round(sum(b["amount"] for b in buckets if b["bucket"] == "Over 90 days"), 2)
        why = (f"{name} owes {money(overdue, c['currency'])} on {count} overdue invoice{'s' if count != 1 else ''}"
               + ("; all of it is more than 90 days old." if old and old >= overdue
                  else f"; {money(old, c['currency'])} of it is more than 90 days old." if old else "."))
        items.append({
            "key": customer_key(org_id, name), "rule": "customer_overdue", "organization_id": org_id,
            "organization_name": c["org"], "currency": c["currency"], "severity": severity, "why": why,
            "buckets": buckets,
            "bill": {"document_id": None, "document_number": None, "document_date": None, "vendor_name": name,
                     "net_purchases": None, "input_vat": None, "outstanding": overdue, "total": overdue,
                     "overdue_count": count},
        })
    items.sort(key=lambda i: -i["bill"]["total"])
    return items, read, len(rows) < CUBE_ROW_LIMIT


# --- position -----------------------------------------------------------------------------------------------------

def position(side: str, org_ids: Sequence[int], today: dt.date, *, subject: str, deadline: float
             ) -> Optional[Dict[str, Any]]:
    """What is overdue now and what falls due in the next 7 days, from the payables or receivables view. Amounts are
    added up only over organizations that share the first one's currency; `organizations` says how many the figures
    cover."""
    if not org_ids:
        return None
    view = SIDES[side]["view"]

    def load(measures: Sequence[str], filters: Sequence[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"dimensions": [f"{view}.currency"], "measures": [f"{view}.{m}" for m in measures]}
        if filters:
            q["filters"] = list(filters)
        return [_plain(r, view) for r in cube_client.load(q, organization_ids=list(org_ids), subject=subject,
                                                          deadline=deadline).rows]

    rows_over = load(("overdue_amount", "overdue_count"))
    rows_soon = load(("outstanding", "open_count"),
                     [{"member": f"{view}.due_date", "operator": "inDateRange",
                       "values": [today.isoformat(), (today + dt.timedelta(days=7)).isoformat()]}])
    # Open documents with no due date can be neither overdue nor due soon; the box says how many there are.
    rows_undated = load(("outstanding", "open_count"), [{"member": f"{view}.due_date", "operator": "notSet"}])
    rows_old = (load(("outstanding", "open_count"),
                     [{"member": f"{view}.aging_bucket", "operator": "equals", "values": ["Over 90 days"]}])
                if side == "receivables" else [])
    currency = next((r.get("currency") for r in rows_over + rows_soon if r.get("currency")), None) or "AED"

    def same(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [r for r in rows if (r.get("currency") or currency) == currency]

    def total(rows: List[Dict[str, Any]], m: str) -> float:
        return round(sum(_num(r.get(m)) or 0.0 for r in same(rows)), 2)

    def count(rows: List[Dict[str, Any]], m: str) -> int:
        return int(sum(_num(r.get(m)) or 0 for r in same(rows)))

    covered = {r.get("organization_id") for r in same(rows_over) + same(rows_soon) if r.get("organization_id") is not None}
    out = {
        "kind": side, "currency": currency,
        "overdue": total(rows_over, "overdue_amount"), "overdue_count": count(rows_over, "overdue_count"),
        "due_next_7_days": total(rows_soon, "outstanding"), "due_next_7_days_count": count(rows_soon, "open_count"),
        "no_due_date": total(rows_undated, "outstanding"), "no_due_date_count": count(rows_undated, "open_count"),
        "organizations": len(covered) or len(org_ids),
        "other_currencies": len({r.get("currency") for r in rows_over + rows_soon} - {currency, None}) > 0,
        "as_of": today.isoformat(),
    }
    if side == "receivables":
        out["over_90_days"] = total(rows_old, "outstanding")
        out["over_90_days_count"] = count(rows_old, "open_count")
    return out
