"""existing.py: sales documents that already exist in Accutax, read as the person.

Two Accutax facts shape this module:

  * GET income/find and PUT income/update look a record up by id only. They never check that the record belongs to
    the organization in the request. So the assistant proves ownership itself: it reads the record and compares its
    organization_id with the organization the person chose, before it copies or changes anything.
  * GET income/find leaves out lines whose catalog item is gone, so a copy could silently lose a line. The list
    endpoint (GET income/list_filter, scoped to the organization) sums every line; a copy is refused when the two
    sums differ.

Amounts are the ones stored with each line. The form stores line_amount as net plus tax, and tax_amount as the tax.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.accutax_api.client import AccutaxApiError, AccutaxClient
from gemini_brain.invoicing.lookups import _num, _rows, unwrap

LIST_PAGE = 50


class DocumentError(Exception):
    """The document cannot be used; the message is safe to show the person."""


@dataclass(frozen=True)
class Row:
    """One line of the organization's list of sales documents."""
    id: int
    number: str
    income_type: str
    contact_name: str
    date: str
    is_draft: bool
    total_value: float
    converted_to_number: str = ""


@dataclass
class Document:
    id: int
    number: str
    income_type: str
    organization_id: int
    contact_id: Optional[int]
    is_draft: bool
    is_posted: bool
    date: str
    due_date: str
    notes: str
    terms: str
    lines: List[Dict[str, Any]] = field(default_factory=list)
    lines_total: float = 0.0
    converted_to_number: str = ""


def _row(r: Dict[str, Any]) -> Optional[Row]:
    try:
        rid = int(r.get("id"))
    except (TypeError, ValueError):
        return None
    return Row(id=rid, number=str(r.get("number") or "").strip(), income_type=str(r.get("income_type") or "").upper(),
               contact_name=str(r.get("contact_name") or "").strip(), date=str(r.get("date") or "")[:10],
               is_draft=bool(r.get("is_draft")), total_value=float(_num(r.get("total_value")) or 0.0),
               converted_to_number=str(r.get("converted_to_number") or "").strip())


def list_documents(client: AccutaxClient, user_id: int, income_type: str, *, number: str = "",
                   contact_name: str = "") -> List[Row]:
    """The organization's documents of one type, newest first. `number` and `contact_name` narrow the list."""
    params: Dict[str, Any] = {"organization_id": client.organization_id, "user_id": str(user_id),
                              "income_type": income_type, "page": 1, "pageSize": LIST_PAGE,
                              "sortBy": "date", "sortOrder": "DESC"}
    if number:
        params["number"] = number
    if contact_name:
        params["contact_name"] = contact_name
    data = unwrap(client.get("income/list_filter", params))
    return [r for r in (_row(x) for x in _rows(data)) if r is not None]


def load(client: AccutaxClient, income_id: int) -> Document:
    """The record, only when it belongs to the client's organization. Raises DocumentError otherwise."""
    try:
        record = unwrap(client.get("income/find", {"income_id": str(int(income_id))}))
    except AccutaxApiError as e:
        if e.kind in ("validation", "not_found"):
            raise DocumentError("That document was not found in this organization.") from None
        raise
    if not isinstance(record, dict) or not record.get("id"):
        raise DocumentError("That document was not found in this organization.")
    if _num(record.get("organization_id")) != float(client.organization_id):
        # The same message as "not found": a record of another organization must not be confirmed to exist.
        raise DocumentError("That document was not found in this organization.")
    lines = []
    for it in record.get("items") or []:
        if not isinstance(it, dict):
            continue
        lines.append({
            "items_id": it.get("items_id"), "description": str(it.get("description") or "").strip(),
            "account_id": it.get("account_id"), "quantity": _num(it.get("quantity")) or 0.0,
            "unit_price": _num(it.get("unit_price")) or 0.0, "discount_percent": _num(it.get("discount_percent")) or 0.0,
            "discount_amount": _num(it.get("discount_amount")) or 0.0, "tax_rate_id": it.get("tax_rate_id"),
            "tax_amount": _num(it.get("tax_amount")) or 0.0, "line_amount": _num(it.get("line_amount")) or 0.0,
            "cost_center_id": it.get("cost_center_id"),
        })
    contact = _num(record.get("contact_id"))
    return Document(
        id=int(record["id"]), number=str(record.get("invoice_number") or record.get("number") or "").strip(),
        income_type=str(record.get("income_type") or "").upper(), organization_id=int(client.organization_id),
        contact_id=int(contact) if contact else None, is_draft=bool(record.get("is_draft")),
        is_posted=bool(record.get("is_posted_to_accounting") or record.get("journal_entry_id")
                       or record.get("is_finalised")),
        date=str(record.get("invoice_date") or "")[:10], due_date=str(record.get("due_date") or "")[:10],
        notes=str(record.get("additional_notes") or ""), terms=str(record.get("terms_and_conditions") or ""),
        lines=lines, lines_total=sum(l["line_amount"] + l["tax_amount"] for l in lines),
        converted_to_number=str(record.get("converted_to_number") or "").strip())


def whole_copy(doc: Document, row: Row) -> bool:
    """True when find returned every line: its sum matches the list's sum over all the lines."""
    return abs(doc.lines_total - row.total_value) < 0.01


def pick(rows: Sequence[Row], number: str) -> List[Row]:
    """The rows whose number is exactly `number` (ignoring case and spaces)."""
    want = "".join(number.split()).casefold()
    return [r for r in rows if "".join(r.number.split()).casefold() == want]
