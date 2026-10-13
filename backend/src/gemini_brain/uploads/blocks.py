"""The card under an answer for each file attached to the question: what it is, its main figures, and what was checked.

Built by code from the read of the file, not written by the model, so the figures on a card are exactly the file's and
the answer need not repeat them. Block type "documents"; the frontend draws it (DocumentCardsBlock).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.uploads.checks import check_label
from gemini_brain.uploads.extract import DocumentExtract

TYPE_LABELS = {
    "tax_invoice": "Tax invoice", "simplified_tax_invoice": "Simplified tax invoice", "receipt": "Receipt",
    "statement": "Statement of account", "contract": "Contract", "budget": "Budget", "other": "Document",
}


def _money(value: Optional[float], currency: Optional[str]) -> str:
    return f"{currency + ' ' if currency else ''}{value:,.2f}"


def _rows(ex: DocumentExtract) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    def add(label: str, value: Any) -> None:
        if value not in (None, ""):
            rows.append({"label": label, "value": str(value)})

    if ex.is_bill:
        add("Supplier", ex.vendor_name)
        add("Supplier TRN", ex.vendor_tax_id)
        add("Buyer", ex.buyer_name)
        add("Number", ex.document_number)
        add("Date", ex.date)
        if ex.subtotal is not None:
            add("Subtotal", _money(ex.subtotal, ex.currency))
        if ex.tax_total is not None:
            add("Tax", _money(ex.tax_total, ex.currency) + (f" ({ex.tax_rate_percent:g}%)" if ex.tax_rate_percent is not None else ""))
        if ex.grand_total is not None:
            add("Total", _money(ex.grand_total, ex.currency))
        if ex.items:
            add("Lines", len(ex.items))
    elif ex.document_type == "statement" or ex.statement_lines:
        add("Counterparty", ex.vendor_name)
        if ex.opening_balance is not None:
            add("Opening balance", _money(ex.opening_balance, ex.currency))
        if ex.closing_balance is not None:
            add("Closing balance", _money(ex.closing_balance, ex.currency))
        add("Lines", len(ex.statement_lines))
    elif ex.text:
        add("Length", f"{len(ex.text):,} characters")
    return rows


def documents_block(docs: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """One card per attached file (an AttachedDocument: .rec and .payload). None when there are none."""
    if not docs:
        return None
    items = []
    for doc in docs:
        ex = DocumentExtract.from_dict(doc.payload.get("extract") or {})
        checks = [{"label": c.get("label") or check_label(c["name"]), "status": c["status"], "detail": c.get("detail", "")}
                  for c in doc.payload.get("checks") or []]
        items.append({
            "id": doc.rec.id, "name": doc.rec.filename, "kind": doc.rec.kind, "pages": doc.rec.pages,
            "type": TYPE_LABELS.get(ex.document_type, "Document"), "rows": _rows(ex), "checks": checks,
            "problems": sum(1 for c in checks if c["status"] == "fail"),
            "note": doc.payload.get("note") or "",
        })
    return {"type": "documents", "items": items}
