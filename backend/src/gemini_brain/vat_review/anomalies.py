"""Odd bills: VAT that does not fit UAE rates, missing details, and amounts out of line.

Every rule works from the fields a bill has today (number, date, supplier, net amount, input VAT, status). Rules that
need the supplier's TRN, the VAT category or a reverse-charge flag wait for those fields (docs/VAT_REVIEW_PLAN.md).

  vat_rate_not_standard   high    VAT is neither 0 nor 5% of the net amount
  vat_wrong_sign          high    VAT and net amount point opposite ways
  vat_without_amount      high    VAT on a bill with no net amount
  missing_supplier        medium  no supplier name
  missing_number          medium  no document number
  future_date             medium  dated after today
  large_for_supplier      medium  far above this supplier's usual bill
  supplier_trn_invalid    medium  VAT on the bill, but the supplier's TRN is missing or does not have 15 digits
  rcm_candidate           medium  supplier outside the UAE and no VAT: the bill may need reverse charge
  vat_on_zero_rated_customer medium  (sales) VAT charged to a customer outside the UAE, or one whose other invoices carry none
  foreign_vat_charged     medium  supplier outside the UAE charged VAT, and has no UAE TRN
  vat_zero                low     no VAT on a bill with an amount (zero-rated, exempt, out of scope, or VAT left off)
  round_amount            low     a round amount, when round amounts are rare
  large_one_off_supplier  low     the supplier's only bill in the period, and a large one

When most bills of an organization share one trait (almost all carry no VAT, for example), that is reported once as
a pattern, not as one finding per bill. Pure functions on bill dicts; no I/O.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

from gemini_brain.vat_review import thresholds as t
from gemini_brain.vat_review.duplicates import vendor_key

SEVERITY = {
    "vat_rate_not_standard": "high", "vat_wrong_sign": "high", "vat_without_amount": "high",
    "missing_supplier": "medium", "missing_number": "medium", "future_date": "medium", "large_for_supplier": "medium",
    "supplier_trn_invalid": "medium", "rcm_candidate": "medium", "foreign_vat_charged": "medium",
    "vat_on_zero_rated_customer": "medium",
    "vat_zero": "low", "round_amount": "low", "large_one_off_supplier": "low",
}
RULES = tuple(SEVERITY)
_RANK = {"high": 0, "medium": 1, "low": 2}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _finding(rule: str, bill: Dict[str, Any], why: str, **details: Any) -> Dict[str, Any]:
    return {"rule": rule, "severity": SEVERITY[rule], "why": why, "details": details,
            "bill": {k: bill.get(k) for k in ("document_number", "document_id", "document_date", "document_type", "status",
                                              "vendor_name", "net_purchases", "input_vat")}}


def reverse_charged(bill: Dict[str, Any]) -> bool:
    """True when the bill has lines with a reverse-charge tax code: the books already account for its VAT under the
    reverse charge (Box 3 and Box 10), so it is not a candidate and its missing VAT is not a finding."""
    return abs(_num(bill.get("reverse_charge_net")) or 0.0) >= 0.005


def foreign_country(bill: Dict[str, Any], side: str = "purchases") -> str:
    """The supplier's country code when the supplier is outside the UAE and the bill is not already reverse-charged,
    else "". Only on purchases, and only when the books give the country at all (the key is present)."""
    if side != "purchases" or "vendor_country_code" not in bill or reverse_charged(bill):
        return ""
    code = str(bill.get("vendor_country_code") or "").strip().upper()
    return code if code and code != "AE" else ""


def _rcm_finding(bill: Dict[str, Any], net: float, code: str) -> Dict[str, Any]:
    self_assessed = round(net * t.STANDARD_RATE, 2)
    return _finding(
        "rcm_candidate", bill,
        f"The supplier is outside the UAE ({code}) and no VAT is recorded on {_money(net)}. "
        f"If this is a service or goods imported for the business, the business must account for the "
        f"VAT itself under the reverse charge: about {_money(self_assessed)} in Box 3, recoverable "
        f"in Box 10 if the cost is fully recoverable.",
        net=net, country=code, self_assessed_vat=self_assessed)


def rcm_candidates(bills: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every rcm_candidate finding, one per bill, never folded into a pattern: bills from a supplier outside the UAE
    with an amount and no VAT (Decree-Law No. 8 of 2017, Article 48). find_anomalies uses the same test."""
    out: List[Dict[str, Any]] = []
    for b in bills:
        net, vat = _num(b.get("net_purchases")), _num(b.get("input_vat")) or 0.0
        code = foreign_country(b)
        if net and code and abs(vat) < 0.005:
            out.append(_rcm_finding(b, net, code))
    return out


def _coded_no_vat(bill: Dict[str, Any], net: float) -> bool:
    """True when the invoice's lines with a no-VAT tax code (zero-rated, exempt, out of scope) cover its whole amount."""
    coded = _num(bill.get("net_sales_coded_no_vat"))
    return coded is not None and abs(coded) >= abs(net) - max(t.ROUNDING_AMOUNT, t.ROUNDING_RATE * abs(net))


def _vat_on_zero_rated_customer(invoices: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sales invoices that charge VAT to a customer outside the UAE, or to a customer whose other invoices in the period
    nearly all carry none (zero-rated exports or designated-zone supplies): the VAT may have been charged by mistake."""
    by_customer: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in invoices:
        if (_num(b.get("net_purchases")) or 0) > 0:
            by_customer[vendor_key(b.get("vendor_name"))].append(b)
    out: List[Dict[str, Any]] = []
    for b in invoices:
        net, vat = _num(b.get("net_purchases")), _num(b.get("input_vat")) or 0.0
        if not net or net <= 0 or vat <= 0.005:
            continue
        code = str(b.get("vendor_country_code") or "").strip().upper()
        others = [x for x in by_customer.get(vendor_key(b.get("vendor_name")), []) if x is not b]
        zero = [x for x in others if abs(_num(x.get("input_vat")) or 0.0) < 0.005]
        if code and code != "AE":
            why = (f"VAT of {_money(vat)} is charged to a customer outside the UAE ({code}). A supply to a customer "
                   "outside the UAE is usually zero-rated (Decree-Law Article 45): check whether this sale should carry VAT.")
        elif len(others) >= t.ZR_CUSTOMER_MIN_OTHERS and len(zero) / len(others) >= t.ZR_CUSTOMER_SHARE:
            why = (f"VAT of {_money(vat)} is charged on this invoice, but {len(zero)} of the customer's {len(others)} other "
                   "invoices in the period carry no VAT (zero-rated, for example exports or designated-zone supplies). "
                   "Check whether this sale should be zero-rated too.")
        else:
            continue
        out.append(_finding("vat_on_zero_rated_customer", b, why, net=net, vat=vat, country=code or None,
                            customer_invoices=len(others), customer_zero_vat=len(zero)))
    return out


def rcm_totals(findings: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals of rcm_candidate findings: bills, net, self-assessed VAT, and the same per supplier (largest first)."""
    by: Dict[str, Dict[str, Any]] = {}
    for f in findings:
        if f.get("rule") != "rcm_candidate":
            continue
        d, b = f.get("details") or {}, f.get("bill") or {}
        name = str(b.get("vendor_name") or "Unknown")
        e = by.setdefault(name, {"vendor_name": name, "country": d.get("country"), "bills": 0, "net": 0.0,
                                 "self_assessed_vat": 0.0})
        e["bills"] += 1
        e["net"] += d.get("net") or 0.0
        e["self_assessed_vat"] += d.get("self_assessed_vat") or 0.0
    rows = sorted(({**e, "net": round(e["net"], 2), "self_assessed_vat": round(e["self_assessed_vat"], 2)}
                   for e in by.values()), key=lambda e: -abs(e["net"]))
    return {"bills": sum(e["bills"] for e in rows), "net": round(sum(e["net"] for e in rows), 2),
            "self_assessed_vat": round(sum(e["self_assessed_vat"] for e in rows), 2), "by_supplier": rows}


def reverse_charged_totals(bills: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Totals of the bills already reverse-charged in the books (lines with a reverse-charge tax code), per supplier."""
    by: Dict[str, Dict[str, Any]] = {}
    for b in bills:
        if not reverse_charged(b):
            continue
        name = str(b.get("vendor_name") or "Unknown")
        e = by.setdefault(name, {"vendor_name": name, "country": b.get("vendor_country_code"), "bills": 0, "net": 0.0})
        e["bills"] += 1
        e["net"] += _num(b.get("reverse_charge_net")) or 0.0
    rows = sorted(({**e, "net": round(e["net"], 2), "self_assessed_vat": round(e["net"] * t.STANDARD_RATE, 2)}
                   for e in by.values()), key=lambda e: -abs(e["net"]))
    net = round(sum(e["net"] for e in rows), 2)
    return {"bills": sum(e["bills"] for e in rows), "net": net, "self_assessed_vat": round(net * t.STANDARD_RATE, 2),
            "by_supplier": rows}


def find_future(bills: Sequence[Dict[str, Any]], today: dt.date) -> List[Dict[str, Any]]:
    """future_date findings for bills dated after today. A review of "this year so far" scans up to today, so the tool
    looks at the bills dated later separately and passes them here."""
    out: List[Dict[str, Any]] = []
    for b in bills:
        try:
            day = dt.date.fromisoformat(str(b.get("document_date") or "")[:10])
        except ValueError:
            continue
        if day > today:
            out.append(_finding("future_date", b, f"The bill is dated {day.isoformat()}, which is after today.",
                                days_ahead=(day - today).days))
    return out


def find_anomalies(bills: Sequence[Dict[str, Any]], today: dt.date,
                   side: str = "purchases") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(findings, patterns) for the usable bills of ONE organization. Findings: most serious first, then largest.
    On sales a customer with no TRN is not a finding (a customer who is not registered has none); a TRN that is not 15
    digits is. The sentences are written for purchases; the tool words them for sales (wording.py)."""
    findings: List[Dict[str, Any]] = []
    patterns: List[Dict[str, Any]] = []
    total = len(bills)
    if not total:
        return findings, patterns

    # --- VAT against the net amount --------------------------------------------------------------------------------
    # Decree-Law No. 8 of 2017, Article 48: Reverse charge mechanism on imported supplies.
    zero_vat: List[Dict[str, Any]] = []
    rcm: List[Dict[str, Any]] = []
    skip_trn_check = set()
    for b in bills:
        net, vat = _num(b.get("net_purchases")), _num(b.get("input_vat")) or 0.0
        if not net:
            if abs(vat) >= 0.005:
                findings.append(_finding("vat_without_amount", b, f"The bill shows VAT of {_money(vat)} but no amount.",
                                         vat=vat))
            continue
        if vat * net < 0:
            findings.append(_finding("vat_wrong_sign", b, f"The VAT ({_money(vat)}) and the amount ({_money(net)}) have "
                                     "opposite signs.", vat=vat, net=net))
        elif abs(vat) < 0.005:
            code = foreign_country(b, side)
            if side == "purchases" and reverse_charged(b):
                continue  # already reverse-charged in the books: no VAT on the bill is right
            if side == "sales" and _coded_no_vat(b, net):
                continue  # every line carries a zero-rated, exempt or out-of-scope tax code: no VAT is right
            if code:
                rcm.append(_rcm_finding(b, net, code))
            else:
                zero_vat.append(_finding("vat_zero", b, f"No VAT is recorded on an amount of {_money(net)}. That is correct "
                                         "only for zero-rated, exempt or out-of-scope purchases.", net=net))
        else:
            code = foreign_country(b, side)
            trn_digits = re.sub(r"\D", "", str(b.get("vendor_trn") or ""))
            if code and vat > 0.005 and len(trn_digits) != t.TRN_DIGITS:
                findings.append(_finding(
                    "foreign_vat_charged", b,
                    f"The supplier is outside the UAE ({code}) and has no UAE TRN, but VAT of {_money(vat)} is recorded. "
                    f"Foreign VAT is not UAE input VAT and can't be claimed in Box 9. The purchase may need reverse charge instead.",
                    net=net, vat=vat, country=code))
                skip_trn_check.add(id(b))
            else:
                expected = round(net * t.STANDARD_RATE, 2)
                if abs(vat - expected) > max(t.ROUNDING_AMOUNT, t.ROUNDING_RATE * abs(net)):
                    findings.append(_finding(
                        "vat_rate_not_standard", b,
                        f"The VAT is {_money(vat)}, which is {vat / net * 100:.1f}% of {_money(net)}. UAE VAT is 5% "
                        f"({_money(expected)} on this amount) or 0%.", net=net, vat=vat, expected_vat=expected,
                        rate_percent=round(vat / net * 100, 2), difference=round(vat - expected, 2)))
    if zero_vat and len(zero_vat) / total >= t.ZERO_VAT_SYSTEMIC_SHARE:
        patterns.append({"rule": "vat_zero", "bills": len(zero_vat), "of": total,
                         "share_percent": round(len(zero_vat) / total * 100),
                         "text": f"{len(zero_vat):,} of {total:,} bills ({len(zero_vat) / total * 100:.0f}%) carry no VAT. "
                                 "If this organization is VAT registered and buys taxable goods or services, input VAT "
                                 "is not being recorded."})
    else:
        findings += zero_vat

    if rcm and len(rcm) / total >= t.ZERO_VAT_SYSTEMIC_SHARE:
        patterns.append({"rule": "rcm_candidate", "bills": len(rcm), "of": total,
                         "share_percent": round(len(rcm) / total * 100),
                         "text": f"{len(rcm):,} of {total:,} bills ({len(rcm) / total * 100:.0f}%) are from suppliers "
                                 "outside the UAE with no VAT recorded. If these are imported services, reverse charge "
                                 "is probably not being recorded."})
    else:
        findings += rcm

    # --- sales: VAT charged to a customer whose sales are normally zero-rated (Decree-Law Article 45) -------------
    if side == "sales":
        findings += _vat_on_zero_rated_customer(bills)

    # --- the supplier's TRN, for bills that carry VAT (only when the books give the TRN at all) ----------------------
    with_vat = [b for b in bills if "vendor_trn" in b and (_num(b.get("input_vat")) or 0) > 0.005 and id(b) not in skip_trn_check]
    bad_trn = []
    for b in with_vat:
        digits = re.sub(r"\D", "", str(b.get("vendor_trn") or ""))
        if len(digits) != t.TRN_DIGITS and not (side == "sales" and len(digits) == 0):
            bad_trn.append((b, len(digits)))
    if bad_trn and len(bad_trn) / len(with_vat) >= t.TRN_SYSTEMIC_SHARE:
        patterns.append({"rule": "supplier_trn_invalid", "bills": len(bad_trn), "of": len(with_vat),
                         "share_percent": round(len(bad_trn) / len(with_vat) * 100),
                         "text": f"{len(bad_trn):,} of {len(with_vat):,} bills with VAT ({len(bad_trn) / len(with_vat) * 100:.0f}%) "
                                 f"show a supplier TRN that is missing or does not have {t.TRN_DIGITS} digits. The TRNs may be "
                                 "entered in another format, or may not be real numbers: check how they are recorded."})
    else:
        for b, n in bad_trn:
            why = (f"VAT of {_money(_num(b.get('input_vat')) or 0)} is claimed, but the supplier has no TRN recorded."
                   if n == 0 else f"VAT is claimed, but the supplier's TRN has {n} digits; a UAE TRN has {t.TRN_DIGITS}.")
            findings.append(_finding("supplier_trn_invalid", b, why, trn_digits=n))

    # --- missing details and dates ---------------------------------------------------------------------------------
    for b in bills:
        if not str(b.get("vendor_name") or "").strip():
            findings.append(_finding("missing_supplier", b, "The bill has no supplier name."))
        if not str(b.get("document_number") or "").strip():
            findings.append(_finding("missing_number", b, "The bill has no document number."))
        try:
            day = dt.date.fromisoformat(str(b.get("document_date") or "")[:10])
        except ValueError:
            continue
        if day > today:
            findings.append(_finding("future_date", b, f"The bill is dated {day.isoformat()}, which is after today.",
                                     days_ahead=(day - today).days))

    # --- amounts out of line ---------------------------------------------------------------------------------------
    nets = [(b, _num(b.get("net_purchases"))) for b in bills]
    by_supplier: Dict[str, List[Tuple[Dict[str, Any], float]]] = defaultdict(list)
    for b, n in nets:
        key = vendor_key(b.get("vendor_name"))
        if key and n and n > 0:
            by_supplier[key].append((b, n))
    for members in by_supplier.values():
        if len(members) < t.SUPPLIER_MIN_BILLS:
            continue
        for i, (b, n) in enumerate(members):
            others = [x for j, (_, x) in enumerate(members) if j != i]
            med = statistics.median(others)
            spread = statistics.median(abs(x - med) for x in others)
            limit = max(t.SUPPLIER_MULTIPLE * med, med + t.SUPPLIER_SPREAD * max(spread, 0.05 * med))
            repeats = sum(1 for x in others if abs(x - n) <= t.SAME_AMOUNT_TOLERANCE * n)
            if n > limit and repeats < t.SAME_AMOUNT_REPEATS:  # an amount the supplier bills often is not out of line
                findings.append(_finding(
                    "large_for_supplier", b,
                    f"The amount {_money(n)} is {n / med:.1f} times this supplier's usual bill (about {_money(med)}).",
                    net=n, supplier_median=round(med, 2), times_usual=round(n / med, 1)))

    positive = [(b, n) for b, n in nets if n and n > 0]
    round_bills = [(b, n) for b, n in positive if n >= t.ROUND_MIN and n % t.ROUND_STEP == 0]
    if round_bills and len(round_bills) / total < t.ROUND_SYSTEMIC_SHARE:
        findings += [_finding("round_amount", b, f"The amount {_money(n)} is a round number of {t.ROUND_STEP:,.0f}s.", net=n)
                     for b, n in round_bills]

    if len(positive) >= t.ONE_OFF_MIN_ORG_BILLS:
        ordered = sorted(n for _, n in positive)
        cut = ordered[int(len(ordered) * t.ONE_OFF_PERCENTILE)]
        counts: Dict[str, int] = defaultdict(int)
        for b, _ in positive:
            counts[vendor_key(b.get("vendor_name"))] += 1
        for b, n in positive:
            key = vendor_key(b.get("vendor_name"))
            if key and counts[key] == 1 and n >= cut:
                findings.append(_finding("large_one_off_supplier", b, "This is the supplier's only bill in the period, "
                                         f"and {_money(n)} is among the largest amounts.", net=n))

    findings.sort(key=lambda f: (_RANK[f["severity"]], -abs(_num(f["bill"].get("net_purchases")) or 0)))
    return findings, patterns
