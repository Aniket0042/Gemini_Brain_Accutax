"""preview.py — Try a bot's checks for real on a period, read-only.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import time
from typing import AbstractSet, Any, Dict, List, Optional

from gemini_brain.bots import cost, watches
from gemini_brain.bots.schema import BotDraft
from gemini_brain.vat_return import tool as vat_return_tool
from gemini_brain.vat_review import tool as vat_review_tool


def try_period(
    draft: BotDraft,
    *,
    preset: str,
    subject: str,
    exclude: AbstractSet[str] = frozenset(),
    deadline_seconds: float = 60.0,
    frequencies: Dict[int, str] | None = None,
    period: Optional[Dict[str, str]] = None,
    unlimited: bool = False,
) -> Dict[str, Any]:
    """Run bot review checks and return position for a preset period (e.g. 'last_month', 'this_quarter').

    `period`: {"start", "end"} ISO dates; when given it is used instead of `preset` (a bot run for one VAT period).
    `unlimited`: every group and finding comes back, not the first 50. A bot run needs all of them to tell which
    problems went away; "Try it on last month" only shows a sample, so it leaves this off.
    `complete` in the result is False when any scan stopped early: then a missing finding does not mean it was fixed."""
    deadline = time.monotonic() + deadline_seconds
    review_calls = watches.review_calls(draft.watches, draft.sensitivity)

    reviews: List[Dict[str, Any]] = []
    bills_checked = 0
    invoices_checked = 0
    groups_found = 0
    findings_found = 0
    period_info: Dict[str, str] = {}
    complete = True

    # Initialize by_watch counts for all watches the draft specifies
    by_watch: Dict[str, int] = {wid: 0 for wid in draft.watches}

    for call_params in review_calls:
        side = call_params.get("side", "purchases")
        req = {
            **call_params,
            "period": period or {"preset": preset},
            "limit": 50,
        }
        res = vat_review_tool.run(
            req,
            organization_ids=draft.org_ids,
            subject=subject,
            deadline=deadline,
            exclude=exclude,
            unlimited=unlimited,
        )
        reviews.append(res)
        if any(not o.get("complete", True) for o in res.get("organizations") or []):
            complete = False

        if not period_info and "period" in res:
            period_info = res["period"]

        if side == "sales":
            invoices_checked += int(res.get("bills_checked") or 0)
        else:
            bills_checked += int(res.get("bills_checked") or 0)

        gf = int(res.get("groups_found") or 0)
        ff = int(res.get("findings_found") or 0)
        groups_found += gf
        findings_found += ff

        # Map findings and groups back to watch IDs on matching side
        groups_by_rule = res.get("groups_by_rule") or {}
        findings_by_rule = res.get("findings_by_rule") or {}

        for wid in draft.watches:
            try:
                w = watches.get(wid)
            except KeyError:
                continue
            if w.side != side:
                continue
            w_total = 0
            for r in w.rules:
                w_total += int(groups_by_rule.get(r) or 0)
                w_total += int(findings_by_rule.get(r) or 0)
            by_watch[wid] += w_total

    # VAT Return check (next_due)
    return_res: Dict[str, Any] = {}
    try:
        ret_out = vat_return_tool.run(
            {"which": "next_due"},
            organization_ids=draft.org_ids,
            subject=subject,
            deadline=deadline,
        )
        return_res = ret_out.get("organizations") or ret_out
    except Exception:
        return_res = {}

    freqs = frequencies if frequencies is not None else {}
    digests_count = cost.runs_per_month(draft, freqs)
    alerts_count = findings_found + groups_found

    return {
        "period": period_info or {"start": "", "end": ""},
        "bills_checked": bills_checked,
        "invoices_checked": invoices_checked,
        "groups_found": groups_found,
        "findings_found": findings_found,
        "by_watch": by_watch,
        "would_send": {
            "alerts": alerts_count,
            "digests": digests_count,
        },
        "return": return_res,
        "reviews": reviews,
        "complete": complete,
    }
