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
from gemini_brain.vat_review.scan import scan_bills

MODES = ("all", "duplicates", "anomalies")
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
#: Time for one organization's scan, so a large book cannot use the whole request.
SCAN_SECONDS = 18.0

RULE_TEXT = {
    "same_number": "same supplier and same document number",
    "same_total_near_date": f"same supplier, same amount, dates within {duplicates.DAYS_APART} days of the first bill",
    "similar_vendor_same_day": "supplier names that look alike, same amount, same date",
    "vat_rate_not_standard": "VAT is neither 0 nor 5% of the amount",
    "vat_wrong_sign": "VAT and amount have opposite signs",
    "vat_without_amount": "VAT on a bill with no amount",
    "missing_supplier": "no supplier name",
    "missing_number": "no document number",
    "future_date": "dated after today",
    "large_for_supplier": "far above the supplier's usual bill",
    "vat_zero": "no VAT on a bill with an amount",
    "round_amount": "a round amount, when round amounts are rare",
    "large_one_off_supplier": "the supplier's only bill in the period, and a large one",
}


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


def _row(kind: str, group: Any, org: str, rule: str, level: str, bill: Dict[str, Any], why: str) -> Dict[str, Any]:
    return {"kind": kind, "group": group, "organization": org, "rule": rule, "level": level,
            "supplier": bill.get("vendor_name"), "document_number": bill.get("document_number"),
            "date": bill.get("document_date"), "amount": bill.get("net_purchases"), "input_vat": bill.get("input_vat"),
            "status": bill.get("status"), "why": why}


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    mode = params.get("mode", "all")
    if mode not in MODES:
        raise ToolInputError(f"mode must be one of {list(MODES)}")
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

    groups: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    patterns: List[Dict[str, Any]] = []
    checked, left_out, notes = 0, 0, []
    per_org: List[Dict[str, Any]] = []
    for org in organization_ids:
        scan = scan_bills(org, start, end, subject=subject, deadline=min(deadline, time.monotonic() + SCAN_SECONDS))
        bills, skipped = duplicates.usable(scan.bills)
        name = next((b.get("organization_name") for b in scan.bills if b.get("organization_name")), None) or f"Organization {org}"
        currency = next((b.get("currency") for b in scan.bills if b.get("currency")), None)
        tag = {"organization_id": org, "organization_name": name, "currency": currency}
        found_groups = duplicates.find_duplicates(bills) if want_dup else []
        found_odd, found_patterns = anomalies.find_anomalies(bills, today) if want_odd else ([], [])
        for item in found_groups + found_odd + found_patterns:
            item.update(tag)
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

    out: Dict[str, Any] = {"mode": mode, "period": {"start": start.isoformat(), "end": end.isoformat()},
                           "bills_checked": checked, "organizations": per_org, "notes": notes}
    rows: List[Dict[str, Any]] = []
    rules: Dict[str, str] = {}
    if want_dup:
        order = {r: i for i, r in enumerate(duplicates.RULES)}
        groups.sort(key=lambda g: (order[g["rule"]], -g["extra_amount"], -g["count"]))
        shown = groups[:limit]
        out.update({"groups_found": len(groups), "groups": shown,
                    "groups_by_rule": {r: sum(1 for g in groups if g["rule"] == r) for r in duplicates.RULES}})
        rows += [_row("duplicate", g["group"], g["organization_name"], g["rule"], g["confidence"], b, g["why"])
                 for g in shown for b in g["bills"]]
        rules.update({r: RULE_TEXT[r] for r in duplicates.RULES})
        if len(groups) > len(shown):
            notes.append(f"Showing the first {len(shown)} of {len(groups)} duplicate groups, most certain first.")
        if not groups:
            notes.append("No duplicate bills were found by these rules. It does not prove there are none: a bill "
                         "entered with a different supplier name, amount or date is not matched.")
    if want_odd:
        shown_f = _spread(findings, limit)
        out.update({"findings_found": len(findings), "findings": shown_f, "patterns": patterns,
                    "findings_by_rule": {r: sum(1 for f in findings if f["rule"] == r) for r in anomalies.RULES
                                         if any(f["rule"] == r for f in findings)},
                    "findings_by_severity": {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}})
        rows += [_row("finding", None, f["organization_name"], f["rule"], f["severity"], f["bill"], f["why"]) for f in shown_f]
        rules.update({r: RULE_TEXT[r] for r in anomalies.RULES})
        if len(findings) > len(shown_f):
            notes.append(f"Showing {len(shown_f)} of {len(findings)} findings: the most serious, with each rule represented.")
        if not findings and not patterns:
            notes.append("No odd bills were found by these rules. Rules that need the supplier's TRN, the VAT category or "
                         "a reverse-charge flag are not applied, because the books do not hold those fields.")
    out.update({"rows": rows, "row_count": len(rows), "rules": rules})
    return out


def tool_spec() -> Dict[str, Any]:
    return {"toolSpec": {
        "name": "review_vat",
        "description": (
            "Reviews the posted purchase bills of the organizations selected in this chat for problems, checking every "
            "bill in the period, not a sample. Mode duplicates: bills that look recorded twice (same supplier and "
            "number is sure; same supplier and amount within 3 days is likely; alike supplier names with the same amount "
            "and date is possible). Mode anomalies: VAT that is neither 0 nor 5% of the amount, wrong signs, missing "
            "supplier or number, future dates, an amount far above the supplier's usual bill, round or one-off large "
            "amounts, and patterns such as almost every bill carrying no VAT. Mode all does both. Use for 'any "
            "duplicate bills', 'check my purchases for problems', 'is the VAT on my bills right', 'what looks odd in my "
            "bills'. It does not check one uploaded file: for that use the file's own checks and list_documents."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODES), "description": "Default all."},
                "period": {"type": "object", "description": "same shape as query_metrics; omit for year to date",
                           "properties": {"preset": {"type": "string"}, "start": {"type": "string"},
                                          "end": {"type": "string"}}},
                "limit": {"type": "integer",
                          "description": f"Most groups and most findings returned (default {DEFAULT_LIMIT}, max {MAX_LIMIT})"},
            },
            "required": [],
        }},
    }}
