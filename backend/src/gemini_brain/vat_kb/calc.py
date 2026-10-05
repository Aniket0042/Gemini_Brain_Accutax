"""Deterministic VAT arithmetic (R10).

The answer model is given these figures in a CALCULATIONS section instead of doing the maths:
in testing, the model computed 5% of a VAT-inclusive amount (AED 3,150) where the law needs
the tax fraction 5/105 (AED 3,000).
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

UAE_RATE = Decimal("0.05")
TAX_FRACTION = UAE_RATE / (1 + UAE_RATE)          # 5/105
_FILS = Decimal("0.01")

# "AED 105,000", "Dhs 5,000", "60 million AED", "1,000 dirhams". The number must start with a digit and
# the currency must be a whole word: a bare "d," ("zero-rated, such") once matched as "Dh" + empty number.
_AED = re.compile(
    r"\b(?:AED|Dhs?)\.?\s*(\d[\d,]*(?:\.\d+)?)\s*(million|m\b|k\b)?"
    r"|\b(\d[\d,]*(?:\.\d+)?)\s*(million|m\b|k\b)?\s*(?:AED|dirhams?)\b", re.I)
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# "a monthly penalty of (14%) per annum": a yearly rate the law charges month by month.
_YEARLY_RATE = re.compile(r"\(?(\d+(?:\.\d+)?)\s*%\)?\s*(?:per annum|per year|a year|annually)\b", re.I)
_MONTHLY = re.compile(r"\bmonth", re.I)
_INCLUSIVE = re.compile(r"\b(incl\w*|inclusive)\b.{0,20}\bvat\b|\bvat[- ]inclusive\b|\bgross\b", re.I)
_EXCLUSIVE = re.compile(r"\b(excl\w*|exclusive|plus|\+)\b.{0,20}\bvat\b|\bnet of vat\b|\bbefore vat\b", re.I)


def aed(value: Decimal) -> str:
    return f"AED {value.quantize(_FILS, rounding=ROUND_HALF_UP):,.2f}"


def vat_in_gross(gross: Decimal) -> Decimal:
    return gross * TAX_FRACTION


def vat_on_net(net: Decimal) -> Decimal:
    return net * UAE_RATE


def recovery_ratio(taxable: Decimal, exempt: Decimal) -> Decimal:
    """Standard apportionment ratio taxable / (taxable + exempt), as a percentage rounded to a whole number."""
    total = taxable + exempt
    if total <= 0:
        raise ValueError("total supplies must be positive")
    return (taxable / total * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _amounts(text: str) -> list[Decimal]:
    out = []
    for m in _AED.finditer(text):
        number, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        value = Decimal(number.replace(",", ""))
        unit = (unit or "").lower()
        if unit in ("million", "m"):
            value *= 1_000_000
        elif unit == "k":
            value *= 1_000
        out.append(value)
    return out


def calculations_for(question: str) -> list[str]:
    """Figures the answer may need, worked out exactly. Empty when the question has no amounts."""
    amounts = _amounts(question)
    if not amounts:
        return []
    lines: list[str] = []
    q = question.lower()
    inclusive = bool(_INCLUSIVE.search(question))
    exclusive = bool(_EXCLUSIVE.search(question))

    # Apportionment: "taxable supplies X, exempt supplies Y, residual input tax Z"
    taxable = re.search(r"taxable supplies[^\d]{0,12}([\d,]+)", question, re.I)
    exempt = re.search(r"exempt supplies[^\d]{0,12}([\d,]+)", question, re.I)
    residual = re.search(r"(?:residual|common) input tax[^\d]{0,12}([\d,]+)", question, re.I)
    if taxable and exempt:
        t, e = (Decimal(x.group(1).replace(",", "")) for x in (taxable, exempt))
        ratio = recovery_ratio(t, e)
        lines.append(f"Standard method recovery ratio = {t:,.0f} / ({t:,.0f} + {e:,.0f}) = {ratio}%")
        if residual:
            r = Decimal(residual.group(1).replace(",", ""))
            lines.append(f"Recoverable residual input tax = {aed(r)} x {ratio}% = {aed(r * ratio / 100)}")

    for amount in amounts[:3]:
        if inclusive or not exclusive:
            lines.append(f"VAT included in {aed(amount)} (VAT-inclusive, x 5/105) = {aed(vat_in_gross(amount))}; "
                         f"value excluding VAT = {aed(amount - vat_in_gross(amount))}")
        if exclusive or not inclusive:
            lines.append(f"VAT on {aed(amount)} (excluding VAT, x 5%) = {aed(vat_on_net(amount))}")

    # Partial payment: "paid 40%" -> unpaid share of a VAT-inclusive invoice and the VAT in it
    paid = re.search(r"paid\s+(\d+(?:\.\d+)?)\s*%", q)
    if paid and amounts and inclusive:
        gross = amounts[0]
        unpaid = gross * (100 - Decimal(paid.group(1))) / 100
        lines.append(f"Unpaid part = {aed(gross)} x {100 - Decimal(paid.group(1))}% = {aed(unpaid)}; "
                     f"VAT in the unpaid part (x 5/105) = {aed(vat_in_gross(unpaid))}")
    return lines


def monthly_equivalents(texts) -> list[str]:
    """The monthly rate of each yearly rate charged monthly in `texts` (source passages), so the model
    quotes "about 1.17% a month" from here instead of dividing by 12 itself."""
    lines, seen = [], set()
    for text in texts:
        if not _MONTHLY.search(text):
            continue
        for m in _YEARLY_RATE.finditer(text):
            rate = Decimal(m.group(1))
            if rate in seen or rate <= 0:
                continue
            seen.add(rate)
            monthly = rate / 12
            lines.append(f"{rate.normalize():f}% per annum charged monthly = "
                         f"{monthly.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}% per month "
                         f"({rate.normalize():f} / 12 = {monthly.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)})")
    return lines
