"""The card under an answer that lists what the VAT review found.

Built by code from the review_vat result of the turn, not written by the model, so every bill number, date and amount on
the card is the books' own and the answer need not repeat them. Block type "vat_review"; the frontend draws it
(VatReviewBlock).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from gemini_brain.vat_review.wording import for_side

#: A short plain title for each rule: what the person sees, never the rule's name.
TITLES = {
    "same_number": "Same bill number twice",
    "same_total_near_date": "Same supplier, same amount, days apart",
    "similar_vendor_same_day": "Alike supplier names, same amount and date",
    "vat_rate_not_standard": "VAT is not 5% or 0%",
    "vat_wrong_sign": "VAT and amount point opposite ways",
    "vat_without_amount": "VAT on a bill with no amount",
    "missing_supplier": "No supplier name",
    "missing_number": "No document number",
    "future_date": "Dated in the future",
    "large_for_supplier": "Far above this supplier's usual bill",
    "supplier_trn_invalid": "Supplier TRN missing or not 15 digits",
    "vat_zero": "No VAT recorded",
    "round_amount": "Round amount",
    "large_one_off_supplier": "Large bill from a one-time supplier",
}
#: What a duplicate's confidence is called on the card.
CONFIDENCE_WORDS = {"high": "Sure", "medium": "Likely", "low": "Possible"}
#: How many bills a card item lists before "and n more".
BILLS_PER_ITEM = 6


def _bill(b: Dict[str, Any], urls: Dict[str, str]) -> Dict[str, Any]:
    number = str(b.get("document_number") or "")
    return {"number": number, "date": b.get("document_date"), "supplier": b.get("vendor_name"),
            "amount": b.get("net_purchases"), "vat": b.get("input_vat"), "status": b.get("status"),
            **({"url": urls[number]} if number in urls else {})}


def review_blocks(data: Sequence[Dict[str, Any]], urls: Dict[str, str] | None = None) -> List[Dict[str, Any]]:
    """One card for each successful review_vat result of the turn (normally one). [] when there is none."""
    urls = urls or {}
    out: List[Dict[str, Any]] = []
    for item in data or []:
        if item.get("tool") != "review_vat":
            continue
        result = item.get("result") or {}
        side = result.get("side") or "purchases"
        items: List[Dict[str, Any]] = []
        for g in result.get("groups") or []:
            bills = g.get("bills") or []
            items.append({"kind": "duplicate", "level": g.get("confidence"), "level_word": CONFIDENCE_WORDS.get(g.get("confidence"), ""),
                          "title": for_side(TITLES.get(g.get("rule"), "Possible duplicate"), side), "why": g.get("why"),
                          "organization": g.get("organization_name"), "extra_amount": g.get("extra_amount"),
                          "currency": g.get("currency"), "count": len(bills),
                          "bills": [_bill(b, urls) for b in bills[:BILLS_PER_ITEM]], "more": max(0, len(bills) - BILLS_PER_ITEM)})
        for f in result.get("findings") or []:
            items.append({"kind": "finding", "level": f.get("severity"), "level_word": str(f.get("severity") or "").capitalize(),
                          "title": for_side(TITLES.get(f.get("rule"), "Odd bill"), side), "why": f.get("why"),
                          "organization": f.get("organization_name"), "currency": f.get("currency"), "count": 1,
                          "bills": [_bill(f.get("bill") or {}, urls)], "more": 0})
        by_severity = result.get("findings_by_severity") or {}
        out.append({
            "type": "vat_review", "side": side, "mode": result.get("mode"), "period": result.get("period"),
            "bills_checked": result.get("bills_checked"),
            "summary": {"duplicate_groups": result.get("groups_found"), "findings": result.get("findings_found"),
                        "high": by_severity.get("high"), "medium": by_severity.get("medium"), "low": by_severity.get("low")},
            "patterns": [p.get("text") for p in result.get("patterns") or [] if p.get("text")],
            "items": items, "notes": [n for n in result.get("notes") or [] if n],
        })
    return out


def without_tables(answer: str) -> str:
    """The answer without its Markdown tables. The review card lists every group and bill, so a table in the answer only
    repeats it; the prompt says so, and this makes it so when the model writes one anyway."""
    lines, out, in_table = (answer or "").split("\n"), [], False
    for line in lines:
        if line.lstrip().startswith("|"):
            in_table = True
            continue
        if in_table and not line.strip():
            in_table = False
            if out and not out[-1].strip():
                continue  # the blank line before the table already separates the paragraphs
        else:
            in_table = False
        out.append(line)
    return "\n".join(out).strip()


#: What each rule is called in a sentence.
PLAIN = {
    "same_number": "same bill number twice", "same_total_near_date": "same supplier and amount a few days apart",
    "similar_vendor_same_day": "alike supplier names with the same amount and day", "vat_rate_not_standard": "VAT not 5% or 0%",
    "vat_wrong_sign": "VAT and amount with opposite signs", "vat_without_amount": "VAT with no amount",
    "missing_supplier": "no supplier name", "missing_number": "no document number", "future_date": "dated in the future",
    "large_for_supplier": "far above the supplier's usual bill", "supplier_trn_invalid": "supplier TRN missing or not 15 digits",
    "vat_zero": "no VAT recorded", "round_amount": "round amount",
    "large_one_off_supplier": "large bill from a one-time supplier",
}
_RULE_NAME = re.compile(r"[\"'“‘`]?\b(" + "|".join(sorted(PLAIN, key=len, reverse=True)) + r")\b[\"'”’`]?")


def plain_rule_names(answer: str) -> str:
    """The answer with any rule name (future_date, vat_zero) put into words. The prompt forbids them; this makes it so."""
    return _RULE_NAME.sub(lambda m: PLAIN[m.group(1)], answer or "")
