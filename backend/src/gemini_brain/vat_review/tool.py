"""review_vat: the agent tool that checks an organization's bills for duplicates.

Same contract as the other figure tools: it runs only on the organizations selected in the chat, returns plain
JSON, and raises ToolInputError for a bad call. Code makes every finding; the model words the answer.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import periods
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.vat_review import duplicates
from gemini_brain.vat_review.scan import scan_bills

MODES = ("duplicates",)
DEFAULT_LIMIT, MAX_LIMIT = 10, 50
#: Time for one organization's scan, so a large book cannot use the whole request.
SCAN_SECONDS = 18.0


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    mode = params.get("mode", "duplicates")
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

    groups: List[Dict[str, Any]] = []
    checked, left_out, notes = 0, 0, []
    per_org: List[Dict[str, Any]] = []
    names: Dict[int, str] = {}
    for org in organization_ids:
        scan = scan_bills(org, start, end, subject=subject, deadline=min(deadline, time.monotonic() + SCAN_SECONDS))
        bills, skipped = duplicates.usable(scan.bills)
        found = duplicates.find_duplicates(bills)
        name = next((b.get("organization_name") for b in scan.bills if b.get("organization_name")), None) or f"Organization {org}"
        currency = next((b.get("currency") for b in scan.bills if b.get("currency")), None)
        for g in found:
            g.update({"organization_id": org, "organization_name": name, "currency": currency})
        names[org] = name
        checked += len(bills)
        left_out += skipped
        groups += found
        per_org.append({"organization_id": org, "organization_name": name, "bills_checked": len(bills),
                        "groups_found": len(found), "complete": scan.complete})
        if not scan.complete:
            notes.append(f"{name}: the scan stopped early ({scan.why_incomplete}); the result may miss duplicates.")
    if left_out:
        notes.append(f"{left_out} voided, cancelled or draft bills were left out.")

    order = {r: i for i, r in enumerate(duplicates.RULES)}
    groups.sort(key=lambda g: (order[g["rule"]], -g["extra_amount"], -g["count"]))
    by_rule = {r: sum(1 for g in groups if g["rule"] == r) for r in duplicates.RULES}
    shown = groups[:limit]
    rows = [{"group": g["group"], "organization": g["organization_name"], "rule": g["rule"], "confidence": g["confidence"],
             "supplier": b.get("vendor_name"), "document_number": b.get("document_number"), "date": b.get("document_date"),
             "amount": b.get("net_purchases"), "input_vat": b.get("input_vat"), "status": b.get("status")}
            for g in shown for b in g["bills"]]
    if len(groups) > len(shown):
        notes.append(f"Showing the first {len(shown)} of {len(groups)} groups, most certain first.")
    if not groups:
        notes.append("No duplicate bills were found by these rules. It does not prove there are none: a bill entered "
                     "with a different supplier name, amount or date is not matched.")
    return {
        "mode": mode, "period": {"start": start.isoformat(), "end": end.isoformat()}, "bills_checked": checked,
        "groups_found": len(groups), "groups_by_rule": by_rule, "organizations": per_org, "groups": shown,
        "rows": rows, "row_count": len(rows), "notes": notes,
        "rules": {"same_number": "same supplier and same document number",
                  "same_total_near_date": f"same supplier, same amount, dates within {duplicates.DAYS_APART} days",
                  "similar_vendor_same_day": "supplier names that look alike, same amount, same date"},
    }


def tool_spec() -> Dict[str, Any]:
    return {"toolSpec": {
        "name": "review_vat",
        "description": (
            "Reviews the posted purchase bills of the organizations selected in this chat for problems. Mode "
            "duplicates finds bills that look recorded twice: same supplier and number (sure), same supplier and "
            "amount within 3 days (likely), supplier names that look alike with the same amount and date (possible). "
            "Use for 'any duplicate bills', 'did we record this twice', 'check my purchases for problems'. It checks "
            "every bill in the period, not a sample. It does not check one uploaded file: for that use list_documents."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(MODES)},
                "period": {"type": "object", "description": "same shape as query_metrics; omit for year to date",
                           "properties": {"preset": {"type": "string"}, "start": {"type": "string"},
                                          "end": {"type": "string"}}},
                "limit": {"type": "integer", "description": f"Most groups returned (default {DEFAULT_LIMIT}, max {MAX_LIMIT})"},
            },
            "required": ["mode"],
        }},
    }}
