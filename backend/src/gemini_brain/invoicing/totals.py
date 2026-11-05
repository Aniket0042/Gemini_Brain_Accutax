"""totals.py: line and document totals for a sales invoice or quote, exactly as the Accutax form computes them.

The Accutax backend stores the line_amount and tax_amount the client sends, so the assistant must compute them the
way the create form does (accutax_bk_frontend/src/components/InvoiceLineItems.jsx calculateLineAmount, and
pages/CreateNew.jsx calculateTotalsFromLineItems), with prices excluding VAT (the form always passes
pricesIncludeTax=false):

    net        = round2(qty * price - qty * price * discount_percent / 100 - discount_amount)
    tax        = round2(net * rate / 100)              (0 when the line has no tax rate)
    line_total = round2(net + tax)
    document:  total = round2(sum line_total), vat = round2(sum tax), subtotal = round2(total - vat)

What is STORED differs from what the form stores. The form saves line_amount as net plus tax, but Accutax's own
backend (the amount due, the invoice page total, the list totals and the journal entry: revenue is credited
line_amount, VAT tax_amount, receivables line_amount + tax_amount) reads line_amount as the NET of the line.
Saving the form's value makes Accutax count the VAT twice (an invoice for 1,225.00 + 220.50 VAT showed 1,666.00).
So the assistant saves line_amount = net, and shows the person net + tax.

round2 is JavaScript's Math.round(n * 100) / 100 on binary floats (half rounds up, toward +infinity), reproduced
here on purpose: a Decimal half-even rounding would differ from the form by a fils on some lines.

Custom columns of the invoice settings (extra charges or discounts per line) are not supported yet; the draft
says so when the organization has any.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional


def round2(n: float) -> float:
    """JavaScript Math.round(n * 100) / 100."""
    return math.floor(n * 100 + 0.5) / 100


@dataclass(frozen=True)
class Line:
    quantity: float
    unit_price: float
    discount_percent: float = 0.0
    discount_amount: float = 0.0
    tax_rate: Optional[float] = None   # percent, e.g. 5.0; None = no tax rate on the line


@dataclass(frozen=True)
class LineTotals:
    net: float
    tax: float
    line_total: float


@dataclass(frozen=True)
class DocumentTotals:
    subtotal: float
    vat: float
    total: float


class TotalsError(ValueError):
    """A line the form would not accept (negative quantity, discount above the line, ...)."""


def check(line: Line) -> None:
    if not math.isfinite(line.quantity) or line.quantity <= 0:
        raise TotalsError("Each line needs a quantity above zero.")
    if not math.isfinite(line.unit_price) or line.unit_price < 0:
        raise TotalsError("A unit price cannot be negative.")
    if not 0 <= line.discount_percent <= 100:
        raise TotalsError("A discount percentage must be between 0 and 100.")
    if line.discount_amount < 0:
        raise TotalsError("A discount amount cannot be negative.")
    if line.discount_amount > line.quantity * line.unit_price * (1 - line.discount_percent / 100) + 1e-9:
        raise TotalsError("A discount cannot be larger than the line.")
    if line.tax_rate is not None and not 0 <= line.tax_rate <= 100:
        raise TotalsError("A VAT rate must be between 0 and 100 percent.")


def line_totals(line: Line) -> LineTotals:
    check(line)
    gross = line.quantity * line.unit_price
    net = round2(gross - gross * (line.discount_percent / 100) - line.discount_amount)
    tax = round2(net * (line.tax_rate / 100)) if line.tax_rate is not None and line.tax_rate >= 0 else 0.0
    return LineTotals(net=net, tax=tax, line_total=round2(net + tax))


def document_totals(lines: Iterable[LineTotals]) -> DocumentTotals:
    lines = list(lines)
    gross = sum(t.line_total for t in lines)
    tax = sum(t.tax for t in lines)
    return DocumentTotals(subtotal=round2(gross - tax), vat=round2(tax), total=round2(gross))


def money(n: float) -> str:
    """12600.0 -> '12,600.00' for the card and the model."""
    return f"{n:,.2f}"


def all_totals(lines: List[Line]) -> tuple:
    per_line = [line_totals(l) for l in lines]
    return per_line, document_totals(per_line)
