"""What a UAE tax invoice must show, as checks on an extract (Executive Regulation of Federal Decree-Law No. 8 of 2017).

Article 59 lists the contents of a tax invoice and of a simplified tax invoice, and says when a simplified one may be used.
Article 60 lists the contents of a tax credit note. Article 53 lists input VAT that cannot be recovered. The wording in the
checks follows the FTA's own documents (the assistant's knowledge base holds them and cites them in the answer).

The supplier's TRN, the date and the currency are judged by the checks in checks.py, so they are not repeated here. Each
function returns a `Check` (pass, fail, or na when it cannot be judged from what was read), or None when it does not apply.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional

from gemini_brain.uploads.checks import TRN_DIGITS, Check, _money, digits_only
from gemini_brain.uploads.extract import DocumentExtract

TAX_INVOICE_WORDS = re.compile(r"tax\s+invoice|فاتورة\s+ضريبية", re.I)
CREDIT_NOTE_WORDS = re.compile(r"tax\s+credit\s+note", re.I)
ANY_CREDIT_NOTE = re.compile(r"credit\s+note|إشعار\s+دائن", re.I)
SIMPLIFIED_WORDS = re.compile(r"simplified", re.I)
#: A simplified tax invoice for a VAT-registered recipient is allowed up to this consideration, in AED (Article 59, clause 5).
SIMPLIFIED_LIMIT_AED = 10_000.0

_ENTERTAINMENT = re.compile(
    r"\b(restaurants?|caf[eé]s?|catering|hotels?|buffet|banquet|dining|lunch|dinner|entertainment|hospitality|concerts?|"
    r"theme park|resort)\b|مطعم|ضيافة|فندق|تموين", re.I)
_VEHICLE = re.compile(r"\b(motor vehicles?|vehicles?|car (rental|hire|lease)|rent(al)? a car|automobiles?)\b", re.I)


def _present(value: Any) -> bool:
    return bool(str(value or "").strip())


def document_kind(ex: DocumentExtract) -> str:
    """credit_note, simplified, receipt or full: the kind of document whose contents are judged."""
    title = ex.document_title or ""
    if ANY_CREDIT_NOTE.search(title):
        return "credit_note"
    if ex.document_type == "simplified_tax_invoice" or SIMPLIFIED_WORDS.search(title):
        return "simplified"
    return "receipt" if ex.document_type == "receipt" else "full"


def uae_document(ex: DocumentExtract) -> bool:
    """A bill in AED, or in another currency from a supplier with a 15-digit UAE TRN, is judged by UAE rules."""
    return (ex.currency or "AED") == "AED" or len(digits_only(ex.vendor_tax_id)) == TRN_DIGITS


def _words(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def required_items(ex: DocumentExtract) -> Check:
    """Whether the document shows what its kind must show."""
    if not uae_document(ex):
        return Check("required_items", "na", f"UAE rules for a tax invoice do not apply to a bill in {ex.currency}.")
    kind = document_kind(ex)
    title = ex.document_title or ""
    missing: List[str] = []
    confirm: List[str] = []
    problems: List[str] = []

    if kind == "credit_note":
        if not CREDIT_NOTE_WORDS.search(title):
            missing.append('the words "Tax Credit Note"')
    elif not TAX_INVOICE_WORDS.search(title):
        missing.append('the words "Tax Invoice"')
    if not _present(ex.vendor_name):
        missing.append("the supplier's name")
    if not _present(ex.vendor_address):
        missing.append("the supplier's address")

    if kind in ("full", "credit_note"):
        if not _present(ex.buyer_name):
            missing.append("the recipient's name")
        if not _present(ex.buyer_address):
            missing.append("the recipient's address")
        if not _present(ex.buyer_tax_id):
            confirm.append("the recipient's TRN (needed when the recipient is registered for VAT)")
    if kind == "full":
        if not _present(ex.document_number):
            missing.append("a sequential or unique invoice number")
        if not ex.items:
            missing.append("a description of the goods or services")
        elif any(i.description in (None, "") for i in ex.items):
            missing.append("a description on every line")
        elif any(i.quantity is None or i.rate is None or i.amount is None for i in ex.items):
            missing.append("the unit price, quantity and amount on every line")
        if ex.tax_rate_percent is None and ex.tax_total is None:
            missing.append("the rate of tax")
        if ex.grand_total is None:
            missing.append("the total amount payable")
        if ex.tax_total is None:
            missing.append("the tax amount")
    elif kind in ("simplified", "receipt"):
        if not ex.items:
            missing.append("a description of the goods or services")
        if ex.grand_total is None:
            missing.append("the total amount")
        if ex.tax_total is None:
            missing.append("the tax amount")
    else:  # credit note
        if not _present(ex.credit_reason):
            missing.append("a brief explanation of why it was issued")
        if ex.tax_total is None:
            missing.append("the VAT amount credited")
        if not _present(ex.original_invoice_reference):
            confirm.append("which tax invoice it corrects")
    if (ex.currency or "AED") != "AED" and kind in ("full", "credit_note") and (ex.total_aed is None or ex.tax_aed is None):
        missing.append("the total and the tax in AED")
    if kind == "simplified" and ex.reverse_charge_statement:
        problems.append("A simplified tax invoice cannot be used when the recipient accounts for the VAT (reverse charge): "
                        "a full tax invoice is needed.")

    head = {"credit_note": "tax credit note", "simplified": "simplified tax invoice", "receipt": "simplified tax invoice",
            "full": "tax invoice"}[kind]
    parts: List[str] = []
    if kind == "receipt":
        parts.append("This is a receipt, not a tax invoice; for input VAT it must show what a simplified tax invoice shows.")
    if missing:
        parts.append(f"A {head} must show {_words(missing)}, and the document does not.")
    parts += problems
    if confirm:
        parts.append(f"To confirm: {_words(confirm)}.")
    if missing or problems or kind == "receipt":
        return Check("required_items", "fail", " ".join(parts))
    return Check("required_items", "pass", " ".join([f"Everything a {head} must show is there."] + parts))


def simplified_limit(ex: DocumentExtract) -> Optional[Check]:
    """Whether a simplified tax invoice may be used: for an unregistered recipient, or up to AED 10,000."""
    if document_kind(ex) != "simplified" or not uae_document(ex):
        return None
    total = ex.grand_total if (ex.currency or "AED") == "AED" else ex.total_aed
    if total is None:
        return Check("simplified_limit", "na", "The total in AED is not shown, so the AED 10,000 limit cannot be judged.")
    limit = f"AED {SIMPLIFIED_LIMIT_AED:,.0f}"
    if _present(ex.buyer_tax_id):
        if total > SIMPLIFIED_LIMIT_AED:
            return Check("simplified_limit", "fail", f"A simplified tax invoice can be used for a VAT-registered recipient only "
                         f"up to {limit}; this one is AED {_money(total)}.")
        return Check("simplified_limit", "pass", f"The recipient is registered and the total, AED {_money(total)}, is within {limit}.")
    if total > SIMPLIFIED_LIMIT_AED:
        return Check("simplified_limit", "na", f"No recipient TRN is shown. A simplified tax invoice is allowed when the "
                     f"recipient is not registered; for a registered recipient the limit is {limit} and this one is "
                     f"AED {_money(total)}.")
    return Check("simplified_limit", "pass", f"The total, AED {_money(total)}, is within {limit}.")


def claim_hint(ex: DocumentExtract) -> Optional[Check]:
    """Costs on which input VAT is blocked in some cases (Article 53): a hint to confirm the purpose, never a verdict."""
    if not uae_document(ex) or (ex.tax_total or 0) <= 0:
        return None
    text = " ".join([ex.vendor_name or "", ex.document_title or ""] + [i.description or "" for i in ex.items])
    hints = []
    if _ENTERTAINMENT.search(text):
        hints.append("This looks like a restaurant, hotel or catering bill. Input VAT cannot be recovered on entertainment for "
                     "people who do not work for the business, such as customers or guests (Article 53). Confirm who it was for.")
    if _VEHICLE.search(text):
        hints.append("This looks like a motor vehicle cost. Input VAT cannot be recovered on a vehicle that is available for "
                     "personal use (Article 53). Confirm how the vehicle is used.")
    return Check("claim_hint", "na", " ".join(hints)) if hints else None
