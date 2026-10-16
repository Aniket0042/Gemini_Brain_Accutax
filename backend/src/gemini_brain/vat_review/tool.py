"""review_vat: the agent tool that checks an organization's bills for duplicates and odd entries.

Same contract as the other figure tools: it runs only on the organizations selected in the chat, returns plain
JSON, and raises ToolInputError for a bad call. Code makes every finding; the model words the answer.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import periods
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.vat_review import anomalies, duplicates
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review.wording import SIDES, for_side
from gemini_brain.vat_review.scan import scan_bills

MODES = ("all", "duplicates", "anomalies")
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
#: Time for one organization's scan, so a large book cannot use the whole request.
SCAN_SECONDS = 18.0
#: How far past the period a check for bills dated in the future looks.
FUTURE_DAYS = 800

RULE_TEXT = {
    "same_number": "same supplier and same document number",
    "same_total_near_date": f"same supplier, same amount, dates within {duplicates.DAYS_APART} days of the first bill",
    "similar_vendor_same_day": "supplier names that look alike, same amount, same date",
    "vat_rate_not_standard": "VAT is neither 0 nor 5% of the amount",
    "vat_wrong_sign": "VAT and amount have opposite signs",
    "vat_without_amount": "VAT on a bill with no amount",
    "missing_supplier": "no supplier name",
    "missing_number": "no document number",
    "supplier_trn_invalid": "VAT on the bill, but the supplier's TRN is missing or not 15 digits",
    "future_date": "dated after today",
    "large_for_supplier": "far above the supplier's usual bill",
    "vat_zero": "no VAT on a bill with an amount",
    "round_amount": "a round amount, when round amounts are rare",
    "large_one_off_supplier": "the supplier's only bill in the period, and a large one",
}


#: A law question for the rules that rest on UAE VAT law. The agent searches the FTA documents with it,
#: so each kind of finding is cited from its own source, not from one general guide. The other rules are not law.
LAW_QUESTIONS = {
    "vat_rate_not_standard": "Federal Decree-Law No. 8 of 2017 standard rate of tax and zero-rated supplies",
    "vat_zero": "Is input tax recoverable on exempt supplies and on zero-rated supplies under the VAT law?",
    "missing_supplier": "What details must a UAE tax invoice show, including the supplier's name, tax registration number and invoice number?",
    "missing_number": "What details must a UAE tax invoice show, including the supplier's name, tax registration number and invoice number?",
    "supplier_trn_invalid": "What details must a UAE tax invoice show, including the supplier's name, tax registration number and invoice number?",
    "same_number": "Federal Decree-Law No. 8 of 2017 recovery of input tax conditions tax invoice",
    "same_total_near_date": "Federal Decree-Law No. 8 of 2017 recovery of input tax conditions tax invoice",
    "similar_vendor_same_day": "Federal Decree-Law No. 8 of 2017 recovery of input tax conditions tax invoice",
    "vat_wrong_sign": "How is a tax credit note recorded and what must it show in the UAE?",
    "vat_without_amount": "How is a tax credit note recorded and what must it show in the UAE?",
    "future_date": "When is the date of supply and when must a tax invoice be issued in the UAE?",
}
MAX_LAW_QUESTIONS = 3


def _law_questions(seen_rules: List[str]) -> List[Dict[str, str]]:
    """The law questions for the rules that fired, most serious first, each question once."""
    out: List[Dict[str, str]] = []
    for rule in seen_rules:
        question = LAW_QUESTIONS.get(rule)
        if question and all(q["question"] != question for q in out) and len(out) < MAX_LAW_QUESTIONS:
            out.append({"for": RULE_TEXT.get(rule, rule), "question": question})
    return out


def _has_trn(organization_ids: Sequence[int], subject: str, deadline: float, side: str = "purchases") -> bool:
    """Whether this Cube gives the other party's TRN (the supplier's on bills, the customer's on sales). A Cube started
    before the field was added does not; the review then skips the TRN check and says so, instead of failing."""
    try:
        from gemini_brain.semantic.catalog import get_catalog
        view = scan_mod.SIDES[side]["view"]
        return scan_mod.member(side, "vendor_trn") in get_catalog(list(organization_ids), subject, deadline).view(view).dimensions
    except Exception:  # noqa: BLE001
        return False


def _future_dated(org: int, end: Any, today: Any, subject: str, deadline: float,
                  with_trn: bool = False, side: str = "purchases") -> List[Dict[str, Any]]:
    """Bills dated after the period's end and after today, which a scan "up to today" never reaches."""
    import datetime as dt
    first = max(today, end) + dt.timedelta(days=1)
    scan = scan_bills(org, first, first + dt.timedelta(days=FUTURE_DAYS), subject=subject,
                      deadline=min(deadline, time.monotonic() + SCAN_SECONDS), with_trn=with_trn, side=side)
    bills, _ = duplicates.usable(scan.bills)
    return anomalies.find_future(bills, today)


def _spread(findings: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The findings to show: most serious first, but one rule never fills the list while others are waiting."""
    queues: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings:
        queues.setdefault(f["rule"], []).append(f)
    order = sorted(queues, key=lambda r: (anomalies._RANK[anomalies.SEVERITY[r]], -len(queues[r])))
    shown: List[Dict[str, Any]] = []
    while len(shown) < limit and any(queues.values()):
        for rule in order:
            if queues[rule] and len(shown) < limit:
                shown.append(queues[rule].pop(0))
    return sorted(shown, key=lambda f: (anomalies._RANK[f["severity"]], order.index(f["rule"])))


def _totals(groups: List[Dict[str, Any]]) -> Dict[str, float]:
    """The amount that would be counted twice, per currency: the sum of the groups' extra amounts."""
    out: Dict[str, float] = {}
    for g in groups:
        cur = g.get("currency") or "AED"
        out[cur] = round(out.get(cur, 0.0) + float(g.get("extra_amount") or 0.0), 2)
    return out


def _money_words(totals: Dict[str, float]) -> str:
    return " and ".join(f"{cur} {amount:,.2f}" for cur, amount in totals.items()) or "nothing"


def _row(kind: str, group: Any, org: str, rule: str, level: str, bill: Dict[str, Any], why: str,
         side: str = "purchases") -> Dict[str, Any]:
    sales = side == "sales"
    return {"kind": kind, "group": group, "organization": org, "rule": rule, "level": level,
            ("customer" if sales else "supplier"): bill.get("vendor_name"), "document_number": bill.get("document_number"),
            "date": bill.get("document_date"), "amount": bill.get("net_purchases"),
            ("output_vat" if sales else "input_vat"): bill.get("input_vat"), "status": bill.get("status"), "why": why}


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    mode = params.get("mode", "all")
    if mode not in MODES:
        raise ToolInputError(f"mode must be one of {list(MODES)}")
    side = params.get("side") or "purchases"
    if side not in SIDES:
        raise ToolInputError(f"side must be one of {list(SIDES)}")
    try:
        limit = max(1, min(int(params.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    except (TypeError, ValueError):
        raise ToolInputError("limit must be a number") from None
    today = periods.today_in(settings.report_timezone)
    try:
        start, end = periods.resolve(params.get("period"), today)
    except ValueError as e:
        raise ToolInputError(str(e)) from e
    want_dup, want_odd = mode in ("all", "duplicates"), mode in ("all", "anomalies")
    only = params.get("rules") or []
    if not isinstance(only, list) or any(r not in RULE_TEXT for r in only):
        raise ToolInputError(f"rules must be a list of: {', '.join(RULE_TEXT)}")
    if only:  # a question about one kind of problem: look for that kind only
        want_dup = want_dup and any(r in duplicates.RULES for r in only)
        want_odd = want_odd and any(r in anomalies.RULES for r in only)
    with_trn = want_odd and _has_trn(organization_ids, subject, deadline, side)

    groups: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    patterns: List[Dict[str, Any]] = []
    checked, left_out, notes = 0, 0, []
    per_org: List[Dict[str, Any]] = []
    for org in organization_ids:
        scan = scan_bills(org, start, end, subject=subject, deadline=min(deadline, time.monotonic() + SCAN_SECONDS),
                          with_trn=with_trn, side=side)
        bills, skipped = duplicates.usable(scan.bills)
        name = next((b.get("organization_name") for b in scan.bills if b.get("organization_name")), None) or f"Organization {org}"
        currency = next((b.get("currency") for b in scan.bills if b.get("currency")), None)
        tag = {"organization_id": org, "organization_name": name, "currency": currency}
        found_groups = duplicates.find_duplicates(bills, side) if want_dup else []
        found_odd, found_patterns = anomalies.find_anomalies(bills, today, side) if want_odd else ([], [])
        if only:
            found_groups = [g for g in found_groups if g["rule"] in only]
            found_patterns = [p for p in found_patterns if p["rule"] in only]
        if want_odd:
            found_odd += _future_dated(org, end, today, subject, deadline, with_trn, side)
        if only:
            found_odd = [f for f in found_odd if f["rule"] in only]
        for item in found_groups + found_odd + found_patterns:
            item.update(tag)
        for item in found_groups + found_odd:  # the sentences are written for bills; a sales review words them for invoices
            item["why"] = for_side(item["why"], side)
        for item in found_patterns:
            item["text"] = for_side(item["text"], side)
        checked += len(bills)
        left_out += skipped
        groups += found_groups
        findings += found_odd
        patterns += found_patterns
        per_org.append({**tag, "bills_checked": len(bills), "complete": scan.complete,
                        **({"duplicate_groups": len(found_groups)} if want_dup else {}),
                        **({"findings": len(found_odd)} if want_odd else {})})
        if not scan.complete:
            notes.append(f"{name}: the scan stopped early ({scan.why_incomplete}); the result may miss problems.")
    if left_out:
        notes.append(f"{left_out} voided, cancelled or draft bills were left out.")
    if want_odd and not with_trn and (not only or "supplier_trn_invalid" in only):
        notes.append("Supplier TRNs were not checked: this book does not provide them.")
    if only:
        notes.append("Only these kinds of problem were looked for: " + "; ".join(RULE_TEXT[r] for r in only) + ".")

    out: Dict[str, Any] = {"mode": mode, "side": side, "period": {"start": start.isoformat(), "end": end.isoformat()},
                           "bills_checked": checked, "organizations": per_org, "notes": notes}
    rows: List[Dict[str, Any]] = []
    rules: Dict[str, str] = {}
    if want_dup:
        order = {r: i for i, r in enumerate(duplicates.RULES)}
        groups.sort(key=lambda g: (order[g["rule"]], -g["extra_amount"], -g["count"]))
        shown = groups[:limit]
        out.update({"groups_found": len(groups), "groups": shown,
                    # Added up by code, per currency, so the answer never sums the groups itself.
                    "amount_counted_twice": {"all_groups": _totals(groups), "shown_groups": _totals(shown)},
                    "groups_by_rule": {r: sum(1 for g in groups if g["rule"] == r) for r in duplicates.RULES}})
        rows += [_row("duplicate", g["group"], g["organization_name"], g["rule"], g["confidence"], b, g["why"], side)
                 for g in shown for b in g["bills"]]
        rules.update({r: RULE_TEXT[r] for r in duplicates.RULES})
        if len(groups) > len(shown):
            notes.append(f"Showing the first {len(shown)} of {len(groups)} duplicate groups, most certain first.")
        if groups:
            notes.append("If every group is a duplicate, " + _money_words(_totals(groups)) + " would be counted twice"
                         + (f" across all {len(groups)} groups; the {len(shown)} shown add up to "
                            + _money_words(_totals(shown)) + "." if len(groups) > len(shown) else "."))
        if not groups:
            notes.append("No duplicate bills were found by these rules. It does not prove there are none: a bill "
                         "entered with a different supplier name, amount or date is not matched.")
    if want_odd:
        shown_f = _spread(findings, limit)
        out.update({"findings_found": len(findings), "findings": shown_f, "patterns": patterns,
                    "findings_by_rule": {r: sum(1 for f in findings if f["rule"] == r) for r in anomalies.RULES
                                         if any(f["rule"] == r for f in findings)},
                    "findings_by_severity": {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}})
        rows += [_row("finding", None, f["organization_name"], f["rule"], f["severity"], f["bill"], f["why"], side)
                 for f in shown_f]
        rules.update({r: RULE_TEXT[r] for r in anomalies.RULES})
        if len(findings) > len(shown_f):
            notes.append(f"Showing {len(shown_f)} of {len(findings)} findings: the most serious, with each rule represented.")
        if not findings and not patterns:
            notes.append("No odd bills were found by these rules. Rules that need the supplier's TRN, the VAT category or "
                         "a reverse-charge flag are not applied, because the books do not hold those fields.")
    fired = [f["rule"] for f in sorted(findings, key=lambda f: anomalies._RANK[f["severity"]])] if want_odd else []
    fired += [g["rule"] for g in groups] if want_dup else []
    seen_rules = list(dict.fromkeys(fired))
    out.update({"rows": rows, "row_count": len(rows), "rules": {k: for_side(v, side) for k, v in rules.items()},
                "law_questions": [{**q, "for": for_side(q["for"], side)} for q in _law_questions(seen_rules)]})
    out["notes"] = [for_side(n, side) for n in notes]
    return out


def tool_spec() -> Dict[str, Any]:
    return {"toolSpec": {
        "name": "review_vat",
        "description": (
            "Reviews the posted purchase bills (side purchases) or sales invoices (side sales) of the organizations "
            "selected in this chat for problems, checking every one in the period, not a sample. Mode duplicates: bills that look recorded twice (same supplier and "
            "number is sure; same supplier and amount within 3 days is likely; alike supplier names with the same amount "
            "and date is possible). Mode anomalies: VAT that is neither 0 nor 5% of the amount, wrong signs, missing "
            "supplier or number, future dates, an amount far above the supplier's usual bill, round or one-off large "
            "amounts, and patterns such as almost every bill carrying no VAT. Mode all does both. Use for 'any "
            "duplicate bills', 'check my purchases for problems', 'is the VAT on my bills right', 'what looks odd in my "
            "bills'. The rule names say supplier and bill, but on side sales the same rules check sales invoices and the "
            "customer: supplier_trn_invalid then checks the customer's TRN (a customer with no TRN is allowed; one that "
            "is not 15 digits is not), and missing_supplier is a missing customer name. It does not check one uploaded "
            "file: for that use the file's own checks and list_documents."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODES), "description": "Default all."},
                "side": {"type": "string", "enum": list(SIDES),
                         "description": "purchases (bills, input VAT; the default) or sales (sales invoices, output VAT)."},
                "period": {"type": "object", "description": "same shape as query_metrics; omit for year to date",
                           "properties": {"preset": {"type": "string"}, "start": {"type": "string"},
                                          "end": {"type": "string"}}},
                "rules": {"type": "array", "items": {"type": "string", "enum": list(RULE_TEXT)},
                          "description": "Look for these kinds of problem only. Use it when the question is about one kind (TRN, "
                                         "no VAT, future dates, missing details, a duplicate type). Omit for a general review."},
                "limit": {"type": "integer",
                          "description": f"Most groups and most findings returned (default {DEFAULT_LIMIT}, max {MAX_LIMIT})"},
            },
            "required": [],
        }},
    }}
