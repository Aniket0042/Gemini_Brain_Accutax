"""Why the ledger's VAT does not match the VAT on the documents, found by matching the two invoice by invoice.

The ledger names the document in each VAT journal ("Invoice INV-1", "Bill EXP-1 - Supplier"), so the journals and the
documents of one period can be matched by number. What is left on one side only, or differs in amount, is the gap, and
each leftover has one of a few causes:

- on the ledger only, and not an issued document today: the invoice or bill was cancelled, voided or is a draft, but its
  VAT journal was never reversed;
- on the ledger only, but the document exists with another date: the journal landed in a different period;
- on the documents only, with no VAT journal anywhere: the document was never posted to the VAT account;
- on the documents only, with a VAT journal in another period: posted in another period;
- on both with a different VAT amount.

Read-only: it uses the same Cube views as the VAT figures, so the same organization check and limits apply. It
returns None when it cannot finish (too many journals, a Cube error), and the VAT figures stand without it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic import cube_client

LIMIT = 5000       # journals or documents per query; more than this and the gap is not explained
CHUNK = 40         # document numbers per filter
MAX_CLASSIFIED = 400
TOLERANCE = 0.01
_NUMBER = re.compile(r"^(?:Invoice|Bill)\s+(\S+)")

SIDES: Dict[str, Dict[str, Any]] = {
    "output": {"view": "sales", "measure": "output_vat", "source": "INCOME", "sign": 1, "what": "sales invoice"},
    "input": {"view": "purchases", "measure": "input_vat", "source": "EXPENSE", "sign": -1, "what": "bill"},
}


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _load(q: Dict[str, Any], org: int, subject: str, deadline: float):
    return cube_client.load(q, organization_ids=[org], subject=subject, deadline=deadline).rows


def _period(dim: str, start: str, end: str) -> List[Dict[str, Any]]:
    return [{"dimension": dim, "dateRange": [start, end]}]


def _vat_filters(source: str) -> List[Dict[str, Any]]:
    return [{"member": "ledger.account_name", "operator": "contains", "values": ["VAT"]},
            {"member": "ledger.account_type", "operator": "equals", "values": ["Asset", "Liability"]},
            {"member": "ledger.source_type", "operator": "equals", "values": [source]}]


def _ledger_amounts(org: int, side: Dict[str, Any], start: str, end: str, subject: str, deadline: float,
                    numbers: Optional[Sequence[str]] = None) -> Optional[Tuple[Dict[str, float], float, List[str]]]:
    """(VAT posted per document number, total of the journals that name no document, examples of those), in the
    period (or, with `numbers`, anywhere in time)."""
    q: Dict[str, Any] = {"dimensions": ["ledger.journal_description"], "measures": ["ledger.debit", "ledger.credit"],
                         "filters": _vat_filters(side["source"]), "limit": LIMIT}
    if numbers is not None:
        q["filters"].append({"member": "ledger.journal_description", "operator": "contains", "values": list(numbers)})
    else:
        q["timeDimensions"] = _period("ledger.transaction_date", start, end)
    rows = _load(q, org, subject, deadline)
    if len(rows) >= LIMIT:
        return None
    out: Dict[str, float] = {}
    unmatched, examples = 0.0, []
    for row in rows:
        text = str(row.get("ledger.journal_description") or "")
        # output VAT is credited by sales; input VAT is debited by bills
        amount = (_num(row.get("ledger.credit")) - _num(row.get("ledger.debit"))) if side["sign"] == 1 else (
            _num(row.get("ledger.debit")) - _num(row.get("ledger.credit")))
        m = _NUMBER.match(text)
        if m:
            out[m.group(1)] = out.get(m.group(1), 0.0) + amount
        elif abs(amount) > TOLERANCE:
            unmatched += amount
            if len(examples) < 3 and text[:80] not in examples:
                examples.append(text[:80])
    return out, round(unmatched, 2), examples


def _doc_amounts(org: int, side: Dict[str, Any], start: str, end: str, subject: str, deadline: float,
                 numbers: Optional[Sequence[str]] = None) -> Optional[Dict[str, float]]:
    """VAT per issued document number, in the period (or, with `numbers`, any date)."""
    view = side["view"]
    q: Dict[str, Any] = {"dimensions": [f"{view}.document_number"], "measures": [f"{view}.{side['measure']}"],
                         "limit": LIMIT}
    if numbers is not None:
        q["filters"] = [{"member": f"{view}.document_number", "operator": "equals", "values": list(numbers)}]
    else:
        q["timeDimensions"] = _period(f"{view}.document_date", start, end)
    rows = _load(q, org, subject, deadline)
    if len(rows) >= LIMIT:
        return None
    return {str(r.get(f"{view}.document_number")): _num(r.get(f"{view}.{side['measure']}")) for r in rows
            if r.get(f"{view}.document_number")}


def _chunks(items: Sequence[str]) -> List[List[str]]:
    return [list(items[i:i + CHUNK]) for i in range(0, len(items), CHUNK)]


def _cause(label: str, numbers: Sequence[str], effect: float) -> Dict[str, Any]:
    return {"cause": label, "documents": len(numbers), "effect_on_gap": round(effect, 2),
            "examples": sorted(numbers)[:3]}


def explain_side(org: int, which: str, start: str, end: str, subject: str, deadline: float) -> Optional[Dict[str, Any]]:
    """The gap between the ledger and the documents for one side ('output' or 'input'), with its causes."""
    side = SIDES[which]
    found = _ledger_amounts(org, side, start, end, subject, deadline)
    docs = _doc_amounts(org, side, start, end, subject, deadline)
    if found is None or docs is None:
        return None
    ledger, unmatched_total, unmatched_examples = found
    # A document with no VAT and no VAT journal is normal (zero-rated, exempt): only leftovers with VAT count.
    only_ledger = sorted(n for n in set(ledger) - set(docs) if abs(ledger[n]) > TOLERANCE)
    only_docs = sorted(n for n in set(docs) - set(ledger) if abs(docs[n]) > TOLERANCE)
    if len(only_ledger) + len(only_docs) > MAX_CLASSIFIED:
        return None

    exists_elsewhere: Dict[str, float] = {}
    for chunk in _chunks(only_ledger):
        exists_elsewhere.update(_doc_amounts(org, side, start, end, subject, deadline, numbers=chunk) or {})
    posted_elsewhere: Dict[str, float] = {}
    for chunk in _chunks(only_docs):
        posted_elsewhere.update((_ledger_amounts(org, side, start, end, subject, deadline, numbers=chunk) or ({},))[0])

    causes: List[Dict[str, Any]] = []
    gone = [n for n in only_ledger if n not in exists_elsewhere]
    moved = [n for n in only_ledger if n in exists_elsewhere]
    never = [n for n in only_docs if n not in posted_elsewhere]
    other_period = [n for n in only_docs if n in posted_elsewhere]
    what = side["what"]
    if gone:
        causes.append(_cause(f"VAT posted for {what}s that are no longer issued (cancelled, voided or draft) and "
                             "whose journal was never reversed", gone, sum(ledger[n] for n in gone)))
    if moved:
        causes.append(_cause(f"VAT posted in this period for {what}s dated in another period", moved,
                             sum(ledger[n] for n in moved)))
    if never:
        causes.append(_cause(f"{what.capitalize()}s with VAT on the document and no VAT journal in the ledger at all",
                             never, -sum(docs[n] for n in never)))
    if other_period:
        causes.append(_cause(f"{what.capitalize()}s whose VAT was posted in another period", other_period,
                             -sum(docs[n] for n in other_period)))
    differing = [n for n in ledger if n in docs and abs(ledger[n] - docs[n]) > TOLERANCE]
    no_vat_doc = [n for n in differing if abs(docs[n]) <= TOLERANCE]
    wrong_side = [n for n in no_vat_doc if ledger[n] < 0]
    ledger_only_vat = [n for n in no_vat_doc if ledger[n] > 0]
    different = [n for n in differing if n not in no_vat_doc]
    opposite = "a debit for a sale" if which == "output" else "a credit for a bill"
    if wrong_side:
        causes.append(_cause(f"The {what}'s VAT is posted on the opposite side ({opposite}) while the document shows "
                             "no VAT", wrong_side, sum(ledger[n] for n in wrong_side)))
    if ledger_only_vat:
        causes.append(_cause(f"The ledger holds VAT for the {what} but the document shows none", ledger_only_vat,
                             sum(ledger[n] for n in ledger_only_vat)))
    if different:
        causes.append(_cause(f"Same {what}, different VAT amount in the ledger and on the document", different,
                             sum(ledger[n] - docs[n] for n in different)))

    if abs(unmatched_total) > TOLERANCE:
        item = _cause(f"VAT journals that name no {what} (reversals, manual or other journals)", [], unmatched_total)
        item["examples"] = unmatched_examples
        causes.append(item)

    ledger_total = round(sum(ledger.values()) + unmatched_total, 2)
    docs_total = round(sum(docs.values()), 2)
    gap = round(ledger_total - docs_total, 2)
    explained = round(sum(c["effect_on_gap"] for c in causes), 2)
    return {"side": which, "documents_vat": docs_total, "ledger_vat": ledger_total, "gap": gap,
            "causes": sorted(causes, key=lambda c: -abs(c["effect_on_gap"])),
            "unexplained": round(gap - explained, 2)}


def explain(org: int, start: str, end: str, subject: str, deadline: float, sides: Sequence[str]) -> Optional[Dict[str, Any]]:
    """{"output": {...}, "input": {...}} for the sides asked; sides it cannot explain are left out. None if none."""
    out: Dict[str, Any] = {}
    for which in sides:
        try:
            result = explain_side(org, which, start, end, subject, deadline)
        except Exception:  # noqa: BLE001 - the explanation is extra; the VAT figures still stand
            result = None
        if result is not None:
            out[which] = result
    return out or None
