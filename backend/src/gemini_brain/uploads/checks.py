"""Checks that code runs on an extract, so the assistant states a fact (the VAT is not 5%) instead of a guess.

Each check is pass, fail or na (cannot be judged from what was read). A model never decides these.
`resolve_parties` says which party on a bill is the organization in scope, which settles supplier against
buyer when the layout leaves it unclear (phase 0, finding 6).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.uploads.extract import DocumentExtract

TOLERANCE = 0.02          # rounding on one amount
VAT_PERCENT = 5.0
TRN_DIGITS = 15


#: What each check is called to a person. The code names (tax_id, vat_rate) never reach an answer or a screen.
LABELS = {
    "items_add_up": "Lines add up",
    "total_adds_up": "Subtotal plus tax equals the total",
    "vat_rate": "VAT is 5%",
    "tax_id": "Supplier's TRN",
    "date_valid": "Date",
    "currency": "Currency",
}


def check_label(name: str) -> str:
    return LABELS.get(name) or str(name).replace("_", " ").capitalize()


@dataclass
class Check:
    name: str
    status: str          # pass | fail | na
    detail: str = ""

    @property
    def label(self) -> str:
        return check_label(self.name)

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "label": self.label}


def run_checks(ex: DocumentExtract, *, today: Optional[date_type] = None) -> List[Check]:
    """The checks for a bill, tax invoice or receipt; none for other documents."""
    if not ex.is_bill:
        return []
    return [
        _items_add_up(ex),
        _total_adds_up(ex),
        _vat_rate(ex),
        _tax_id(ex),
        _date_valid(ex, today or date_type.today()),
        _currency(ex),
    ]


def problem_sentences(checks: Sequence[Any]) -> List[str]:
    """Each failed check as one plain sentence, for a tile: "VAT is 5%. VAT of 52.40 is not 5% of 1,000.00 (5% is 50.00)."
    `checks`: Check objects or the dicts stored with a read."""
    out = []
    for c in checks or []:
        d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
        if d.get("status") == "fail":
            out.append(f"{d.get('label') or check_label(d['name'])}. {d.get('detail', '')}".strip())
    return out


def failed(checks: Sequence[Check]) -> List[Check]:
    return [c for c in checks if c.status == "fail"]


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _items_add_up(ex: DocumentExtract) -> Check:
    amounts = [i.amount for i in ex.items if i.amount is not None]
    if not amounts or len(amounts) != len(ex.items):
        return Check("items_add_up", "na", "Not every line has an amount.")
    total = round(sum(amounts), 2)
    # Lines may be priced without tax or with it; either sum may match.
    targets = [t for t in (ex.subtotal, ex.grand_total) if t is not None]
    if not targets:
        return Check("items_add_up", "na", "The document has no subtotal or total to compare with.")
    slack = TOLERANCE * max(1, len(amounts))
    if any(abs(total - t) <= slack for t in targets):
        return Check("items_add_up", "pass", f"The lines add up to {_money(total)}.")
    return Check("items_add_up", "fail",
                 f"The lines add up to {_money(total)}, but the document shows {' and '.join(_money(t) for t in targets)}.")


def _total_adds_up(ex: DocumentExtract) -> Check:
    if ex.subtotal is None or ex.tax_total is None or ex.grand_total is None:
        return Check("total_adds_up", "na", "The subtotal, tax or total is missing.")
    expected = round(ex.subtotal + ex.tax_total, 2)
    if abs(expected - ex.grand_total) <= max(TOLERANCE, 0.05):  # many shops round the total to 0.05
        return Check("total_adds_up", "pass", f"{_money(ex.subtotal)} + {_money(ex.tax_total)} = {_money(ex.grand_total)}.")
    if ex.tax_total > 0 and abs(ex.subtotal - ex.grand_total) <= max(TOLERANCE, 0.05):
        # Receipts often print the total with the tax already in it, then show the tax as a part of that total.
        return Check("total_adds_up", "pass", f"The total {_money(ex.grand_total)} includes the tax of {_money(ex.tax_total)}.")
    return Check("total_adds_up", "fail",
                 f"{_money(ex.subtotal)} + {_money(ex.tax_total)} is {_money(expected)}, but the total shows {_money(ex.grand_total)}.")


def _is_uae(ex: DocumentExtract) -> bool:
    return (ex.currency or "AED") == "AED"


def _vat_rate(ex: DocumentExtract) -> Check:
    if not _is_uae(ex):
        return Check("vat_rate", "na", f"UAE VAT does not apply to a bill in {ex.currency}.")
    if ex.subtotal is None or ex.tax_total is None:
        return Check("vat_rate", "na", "The subtotal or tax is missing.")
    if abs(ex.tax_total) < 0.005:
        return Check("vat_rate", "pass", "No VAT is charged (zero-rated or exempt).")
    expected = round(ex.subtotal * VAT_PERCENT / 100, 2)
    if abs(expected - ex.tax_total) <= TOLERANCE:
        return Check("vat_rate", "pass", f"VAT of {_money(ex.tax_total)} is 5% of {_money(ex.subtotal)}.")
    if ex.grand_total is not None and abs(ex.subtotal - ex.grand_total) <= 0.05:  # tax-inclusive: 5% of the part before tax
        inclusive = round(ex.subtotal * VAT_PERCENT / (100 + VAT_PERCENT), 2)
        if abs(inclusive - ex.tax_total) <= TOLERANCE:
            return Check("vat_rate", "pass", f"VAT of {_money(ex.tax_total)} is 5% of the price before tax.")
    return Check("vat_rate", "fail",
                 f"VAT of {_money(ex.tax_total)} is not 5% of {_money(ex.subtotal)} (5% is {_money(expected)}).")


def digits_only(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def _tax_id(ex: DocumentExtract) -> Check:
    if not _is_uae(ex):
        return Check("tax_id", "na", f"A UAE TRN is not expected on a bill in {ex.currency}.")
    raw = ex.vendor_tax_id or ""
    if not raw:
        return Check("tax_id", "fail", "The supplier's tax registration number (TRN) is not on the document.")
    if re.search(r"[xX*#]{3,}", raw):
        return Check("tax_id", "na", "The supplier's TRN is hidden on the document.")
    n = len(digits_only(raw))
    if n == TRN_DIGITS:
        return Check("tax_id", "pass", "The supplier's TRN has 15 digits.")
    return Check("tax_id", "fail", f"The supplier's TRN has {n} digits; a UAE TRN has {TRN_DIGITS}.")


def _date_valid(ex: DocumentExtract, today: date_type) -> Check:
    if not ex.date:
        return Check("date_valid", "fail", "The document has no readable date.")
    try:
        d = datetime.strptime(ex.date, "%Y-%m-%d").date()
    except ValueError:
        return Check("date_valid", "fail", f"{ex.date} is not a date.")
    if d > today:
        return Check("date_valid", "fail", f"The date {ex.date} is in the future.")
    return Check("date_valid", "pass", f"The date is {ex.date}.")


def _currency(ex: DocumentExtract) -> Check:
    if ex.currency:
        return Check("currency", "pass", f"The currency is {ex.currency}.")
    return Check("currency", "fail", "The currency is not shown.")


# ── which party is us ──────────────────────────────────────────────────────────

_NOISE = re.compile(r"\b(llc|l\.l\.c|ltd|limited|pty|inc|fze|fzco|co|company|trading|est|establishment|"
                    r"sdn|bhd|pvt|the|of|and)\b", re.I)


def _name_key(name: Optional[str]) -> set:
    cleaned = _NOISE.sub(" ", re.sub(r"[^\w\s]", " ", (name or "").lower()))
    return set(cleaned.split())


def _flat(text: Optional[str]) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _same_party(name: Optional[str], tax_id: Optional[str], own_names: Sequence[str], own_tax_ids: Sequence[str],
                address: Optional[str] = None) -> bool:
    """The tax ID, or the organization's name, matches the party. A name may sit in the address line (a person's
    name above the company's), so the address counts when all of the organization's name is in it."""
    tid = digits_only(tax_id)
    if tid and len(tid) >= 9 and any(tid == digits_only(t) for t in own_tax_ids):
        return True
    key = _name_key(name)
    around = key | _name_key(address)
    for own in own_names:
        okey = _name_key(own)
        if not okey:
            continue
        if key and len(key & okey) / min(len(key), len(okey)) >= 0.75:
            return True
        if len(" ".join(okey)) >= 4 and okey <= around:
            return True
        own_flat = _flat(own)
        if len(own_flat) >= 8 and own_flat in _flat(f"{name or ''} {address or ''}"):  # the full name, as printed
            return True
    return False


def resolve_parties(ex: DocumentExtract, own_names: Sequence[str] = (), own_tax_ids: Sequence[str] = ()) -> Dict[str, Any]:
    """Compare both parties with the organization in scope.

    `direction` is 'purchase' when the organization is the buyer, 'sale' when it is the supplier (an invoice it
    issued) and 'unknown' when neither or both match; `basis` says why, in words the answer can use. When the
    model has the parties the wrong way round (phase 0: it took the buyer as supplier on 7 of 10 runs of a
    layout with callouts), this is what corrects it: the party that is not the organization is the other side.
    """
    if not own_names and not own_tax_ids:  # the organization's name could not be loaded: nothing to compare with
        return {"direction": "unknown", "supplier": ex.vendor_name, "supplier_tax_id": ex.vendor_tax_id,
                "basis": "This organization's name is not available right now, so the parties could not be compared with it. "
                         "The supplier is the one the document names as issuer."}
    vendor_is_us = _same_party(ex.vendor_name, ex.vendor_tax_id, own_names, own_tax_ids, ex.vendor_address)
    buyer_is_us = _same_party(ex.buyer_name, ex.buyer_tax_id, own_names, own_tax_ids)
    if buyer_is_us and not vendor_is_us:
        return {"direction": "purchase", "supplier": ex.vendor_name, "supplier_tax_id": ex.vendor_tax_id,
                "basis": "The buyer on the document is this organization."}
    if vendor_is_us and not buyer_is_us:
        return {"direction": "sale", "supplier": ex.vendor_name, "supplier_tax_id": ex.vendor_tax_id,
                "basis": "The supplier on the document is this organization, so it is an invoice it issued."}
    if vendor_is_us and buyer_is_us:
        return {"direction": "unknown", "supplier": ex.vendor_name, "supplier_tax_id": ex.vendor_tax_id,
                "basis": "Both parties look like this organization."}
    return {"direction": "unknown", "supplier": ex.vendor_name, "supplier_tax_id": ex.vendor_tax_id,
            "basis": "Neither party's name matches this organization's name (a file may use a trading name), so the supplier is the one the document names as issuer."}
