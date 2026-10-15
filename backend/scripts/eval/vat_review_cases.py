"""VAT review evaluation data (docs/VAT_REVIEW_PLAN.md, phase 4): a planted book, the questions, and how answers are judged.

The test organizations' books are read-only, so problems cannot be planted in them. Instead the evaluation replaces what
the review sees for one organization with PLANTED_BOOK: about 60 bills of which every problem is known. The rest of the
stack is real: the scan, the rules, the tool, the agent and the model. The cases at the end say which questions to ask,
which bills the card must hold and what the answer must and must not say.

Planted bills have document numbers that start with PLANT- (the one with no number is named by its supplier).
"""
from __future__ import annotations

import datetime as dt
import re
import zlib
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

ORG = 25
ORG_NAME = "Planted Trading LLC"
#: Dates are in 2026, before the end of a year-to-date period, except the future-dated bill.
FUTURE_BILL_DATE = "2026-12-24"


VALID_TRN = "100123456700003"


def _bill(number: str, vendor: str, date: str, net: float, vat: Optional[float] = None,
          trn: Optional[str] = VALID_TRN) -> Dict[str, Any]:
    vat = round(net * 0.05, 2) if vat is None else vat
    return {"document_number": number, "document_id": str(900000 + zlib.crc32((number + vendor + date).encode()) % 99999),
            "document_date": date, "document_type": "BILL", "status": "ACCEPTED", "vendor_name": vendor,
            "net_purchases": net, "input_vat": vat, "currency": "AED", "organization_name": ORG_NAME, "vendor_trn": trn}


def planted_book() -> List[Dict[str, Any]]:
    """Every bill the review sees, with the problems the rules must find and the bills they must leave alone."""
    bills: List[Dict[str, Any]] = []
    # 20 clean bills: 5% VAT, different suppliers, different amounts, no round numbers.
    for i in range(1, 21):
        bills.append(_bill(f"PLANT-C-{i:02d}", f"Clean Supplier {i:02d}", f"2026-{(i % 9) + 1:02d}-{(i * 3) % 27 + 1:02d}",
                           round(1100 + 137.37 * i, 2)))
    # duplicates
    bills += [_bill("PLANT-D-1", "Planted Exact Supplies", "2026-03-02", 3300.0),
              _bill("PLANT-D-1", "Planted Exact Supplies", "2026-03-20", 3300.0),               # same supplier and number
              _bill("PLANT-L-1", "Planted Likely Supplies", "2026-03-10", 1850.0),
              _bill("PLANT-L-2", "Planted Likely Supplies", "2026-03-12", 1850.0),               # same amount, 2 days apart
              _bill("PLANT-P-1", "Zenith Stationery", "2026-03-15", 2310.0),
              _bill("PLANT-P-2", "Zenith Stationary", "2026-03-15", 2310.0)]                    # alike names, same amount and day
    # VAT problems
    bills += [_bill("PLANT-V-1", "Planted Rate Test", "2026-03-21", 2000.0, 400.0),             # 20%
              _bill("PLANT-V-2", "Planted Rate Test Two", "2026-03-22", 3010.0, 361.2),         # 12%
              _bill("PLANT-S-1", "Planted Sign Test", "2026-03-23", 1010.0, -50.5),
              _bill("PLANT-N-1", "Planted No Amount", "2026-03-24", 0.0, 25.0),
              _bill("PLANT-Z-1", "Planted Zero Rated", "2026-03-25", 1510.0, 0.0)]
    # the supplier's TRN: 9 digits and none on bills with VAT; an odd TRN on a bill with no VAT is not flagged
    bills += [_bill("PLANT-T-1", "Planted Short Trn", "2026-04-10", 2020.0, trn="123456789"),
              _bill("PLANT-T-2", "Planted No Trn", "2026-04-11", 2030.0, trn=None),
              _bill("PLANT-T-3", "Planted Zero Vat Trn", "2026-04-12", 2040.0, vat=0.0, trn="12345")]
    # missing details and a future date
    bills += [_bill("PLANT-M-1", "", "2026-04-02", 777.0), _bill("", "Planted No Number", "2026-04-03", 888.0),
              _bill("PLANT-F-1", "Planted Future", FUTURE_BILL_DATE, 1234.0)]
    # one supplier with a usual bill of about 1,000 and one of 9,500
    bills += [_bill(f"PLANT-R-{i}", "Planted Regular", f"2026-02-{i:02d}", 1000.0 + 10 * i + 0.5) for i in range(1, 9)]
    bills.append(_bill("PLANT-R-9", "Planted Regular", "2026-04-06", 9500.5))
    return bills


#: What the rules must find in PLANTED_BOOK: rule -> the bills (by number, or by supplier when there is no number).
EXPECTED = {
    "same_number": ["PLANT-D-1"],
    "same_total_near_date": ["PLANT-L-1", "PLANT-L-2"],
    "similar_vendor_same_day": ["PLANT-P-1", "PLANT-P-2"],
    "vat_rate_not_standard": ["PLANT-V-1", "PLANT-V-2"],
    "vat_wrong_sign": ["PLANT-S-1"],
    "vat_without_amount": ["PLANT-N-1"],
    "supplier_trn_invalid": ["PLANT-T-1", "PLANT-T-2"],
    "vat_zero": ["PLANT-Z-1", "PLANT-T-3"],
    "missing_supplier": ["PLANT-M-1"],
    "missing_number": ["Planted No Number"],
    "future_date": ["PLANT-F-1"],
    "large_for_supplier": ["PLANT-R-9"],
}
CLEAN = [f"PLANT-C-{i:02d}" for i in range(1, 21)] + [f"PLANT-R-{i}" for i in range(1, 9)]


def key_of(bill: Dict[str, Any]) -> str:
    return bill.get("document_number") or bill.get("vendor_name") or ""


@contextmanager
def planted_cube(organization_id: int = ORG, bills: Optional[List[Dict[str, Any]]] = None) -> Iterator[None]:
    """For the length of the block, the review's scan of `organization_id` sees only the planted bills. Every other
    Cube call, and every other organization, goes to the real Cube."""
    from gemini_brain.semantic import cube_client
    from gemini_brain.vat_review import tool as review_tool

    book = bills if bills is not None else planted_book()
    real = cube_client.load
    real_has_trn = review_tool._has_trn

    def load(query, *, organization_ids, subject, deadline):
        dims = query.get("dimensions") or []
        if list(organization_ids) == [organization_id] and query.get("limit") == 500 and "purchases.document_id" in dims:
            a, b = query["timeDimensions"][0]["dateRange"]
            rows = [{f"purchases.{k}": v for k, v in x.items()} for x in book if a <= x["document_date"] <= b][:500]
            return SimpleNamespace(rows=rows, query=query, elapsed_ms=0, last_refresh_time=None, used_pre_aggregations=[])
        return real(query, organization_ids=organization_ids, subject=subject, deadline=deadline)

    cube_client.load = load
    review_tool._has_trn = lambda *a, **k: True  # the planted bills carry a supplier TRN
    try:
        yield
    finally:
        cube_client.load = real
        review_tool._has_trn = real_has_trn


# ── the questions ─────────────────────────────────────────────────────────────────────────────────

RULE_NAMES = ("same_number", "same_total_near_date", "similar_vendor_same_day", "vat_rate_not_standard", "vat_wrong_sign",
              "vat_without_amount", "missing_supplier", "missing_number", "future_date", "large_for_supplier", "vat_zero",
              "round_amount", "large_one_off_supplier", "supplier_trn_invalid")
#: An answer about duplicates or odd bills must say the findings need a person to confirm them.
HEDGES = ("confirm", "verify", "not confirmed", "check each", "needs a person", "need a person", "review", "worth checking",
          "doesn't prove", "does not prove", "not prove")


def case(cid: str, question: str, **kw: Any) -> Dict[str, Any]:
    """Keys: org, planted (use the planted book), tools_any, tools_none, card_has (bills the card must hold),
    any_of (the answer holds at least one), must (all), must_not, blocks (types that must come back), review (an
    answer about duplicates or odd bills: hedge, no rule names, no table)."""
    return {"id": cid, "question": question, "org": ORG, "planted": True, "tools_any": [], "tools_none": [], "card_has": [],
            "any_of": [], "must": [], "must_not": [], "blocks": [], "review": False, **kw}


CASES: List[Dict[str, Any]] = [
    case("dup_all", "Are there any duplicate bills this year?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-D-1", "PLANT-L-1", "PLANT-L-2", "PLANT-P-1", "PLANT-P-2"], blocks=["vat_review"]),
    case("dup_same_number", "Did we record any bill twice with the same bill number?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-D-1"], any_of=["sure", "same document number", "same bill number", "same supplier and number"]),
    case("dup_vendor", "Is Zenith Stationery billing us twice?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-P-1", "PLANT-P-2"], any_of=["zenith"]),
    case("dup_amount", "How much could we be double-counting from duplicate bills?", tools_any=["review_vat"], review=True,
         any_of=["3,300", "1,850", "2,310"]),
    case("vat_right", "Is the VAT on my purchase bills right this year?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-V-1", "PLANT-V-2"], any_of=["5%"], must_not=["trn is valid", "trns are valid"]),
    case("vat_signs", "Any bills with negative VAT, or VAT but no amount?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-S-1", "PLANT-N-1"]),
    # "Which bills have no VAT recorded?" is also a plain list question; list_documents answers it from the real books.
    case("vat_none", "Review my purchase bills for any with VAT left off", tools_any=["review_vat"], review=True,
         card_has=["PLANT-Z-1"]),
    case("missing", "Are there any bills with no supplier name or no document number?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-M-1", "Planted No Number"]),
    case("future", "Are there any bills dated in the future?", tools_any=["review_vat"], review=True, card_has=["PLANT-F-1"]),
    case("large", "Which bills look unusually large for their supplier?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-R-9"]),
    case("odd_all", "What looks odd in my purchase bills?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-V-1", "PLANT-S-1", "PLANT-N-1"], blocks=["vat_review"]),
    case("problems", "Check my purchases for problems", tools_any=["review_vat"], review=True,
         card_has=["PLANT-D-1", "PLANT-V-1"], blocks=["vat_review"]),
    case("trn", "Is the supplier TRN valid on my bills?", tools_any=["review_vat"], review=True,
         card_has=["PLANT-T-1", "PLANT-T-2"], any_of=["15 digits", "15-digit"],
         must_not=["trns are valid", "all trns are valid", "registered with the fta"]),
    case("export_dup", "Export the duplicate bills to Excel", tools_any=["review_vat"], blocks=["vat_review", "artifact"]),
    case("export_odd", "Export all odd bills to Excel", tools_any=["review_vat"], blocks=["artifact"]),
    # questions that must not go to the review
    case("ctl_total", "What is my total purchases this year?", tools_none=["review_vat"]),
    case("ctl_largest", "List the 5 largest bills this year", tools_none=["review_vat"], tools_any=["list_documents"]),
    # a law question may be answered by the law route before the agent runs, so no tool is required
    case("ctl_law", "What is the standard VAT rate in the UAE?", tools_none=["review_vat"], any_of=["5%", "5 %"]),
    # real data: a noisy seeded book, a book with no VAT at all, a real book with no duplicates
    case("real_dup", "Are there any duplicate bills in 2023?", org=27, planted=False, tools_any=["review_vat"], review=True,
         any_of=["likely"], blocks=["vat_review"]),
    case("real_pattern", "Check my purchases for odd bills this year", org=26, planted=False, tools_any=["review_vat"], review=True,
         any_of=["no vat", "not recorded", "carry no vat", "without vat"]),
    case("real_none", "Check for duplicate bills in 2025", org=25, planted=False, tools_any=["review_vat"], review=True,
         any_of=["none were found", "no duplicate", "no duplicates", "none found", "not found"]),
]


# ── judging (pure) ────────────────────────────────────────────────────────────────────────────────

def card_numbers(blocks: List[Dict[str, Any]]) -> List[str]:
    """Every bill key (number, or supplier when it has none) on the review cards of a response."""
    out: List[str] = []
    for b in blocks or []:
        if b.get("type") == "vat_review":
            for item in b.get("items") or []:
                for bill in item.get("bills") or []:
                    out.append(bill.get("number") or bill.get("supplier") or "")
    return out


def judge(case_: Dict[str, Any], answer: str, tools: List[str], blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = (answer or "").lower().replace(" ", " ").replace(" ", " ")  # the model writes "5 %" with a non-breaking space
    checks: Dict[str, bool] = {}
    for t in case_["tools_any"]:
        checks[f"called {t}"] = t in tools
    for t in case_["tools_none"]:
        checks[f"did not call {t}"] = t not in tools
    held = card_numbers(blocks)
    for number in case_["card_has"]:
        checks[f"card holds {number}"] = number in held
    if case_["any_of"]:
        checks["says one of: " + " / ".join(case_["any_of"][:3])] = any(p.lower() in text for p in case_["any_of"])
    for p in case_["must"]:
        checks[f"says {p}"] = p.lower() in text
    for p in case_["must_not"]:
        checks[f"does not say {p}"] = p.lower() not in text
    for t in case_["blocks"]:
        checks[f"returns a {t} block"] = any(b.get("type") == t for b in blocks or [])
    if case_["review"]:
        checks["says a person must confirm"] = any(h in text for h in HEDGES)
        checks["no rule name in the answer"] = not any(r in text for r in RULE_NAMES)
        checks["no table in the answer"] = not re.search(r"^\s*\|?\s*-{3,}\s*\|", answer or "", re.M)
    return {"ok": all(checks.values()), "checks": checks}
