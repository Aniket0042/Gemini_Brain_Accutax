"""The card under an answer that presents the UAE VAT 201 draft return.

Built by code from the vat_201_draft result of the turn, not written by the model, so every box figure
is grounded in the books and the answer need not repeat them. Block type "vat_201"; the frontend draws it
(Vat201Block).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

DISCLAIMER = "Draft prepared by Accutax from your books. Not an FTA document. Check every figure before filing."


def form201_blocks(data: Sequence[Dict[str, Any]], urls: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """One card for each successful vat_201_draft result of the turn (normally one). [] when none."""
    urls = urls or {}
    out: List[Dict[str, Any]] = []

    for item in data or []:
        if not isinstance(item, dict) or item.get("tool") != "vat_201_draft":
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue

        orgs = result.get("organizations") or []
        if not isinstance(orgs, list):
            continue

        period_title = ""
        enriched_orgs: List[Dict[str, Any]] = []

        for o in orgs:
            if not isinstance(o, dict):
                continue
            period = o.get("period") or {}
            start = period.get("start")
            end = period.get("end")
            if not period_title and start and end:
                period_title = f"{start} to {end}"

            rc = o.get("reverse_charge") or {}
            candidates = rc.get("candidates") or []
            enriched_candidates = []
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                num = str(c.get("document_number") or "")
                c_copy = dict(c)
                if num and num in urls:
                    c_copy["url"] = urls[num]
                enriched_candidates.append(c_copy)

            enriched_rc = {
                **rc,
                "candidates": enriched_candidates,
            }

            enriched_orgs.append({
                "organization_id": o.get("organization_id"),
                "organization_name": o.get("organization_name"),
                "emirate": o.get("emirate"),
                "period": period,
                "frequency": o.get("frequency"),
                "frequency_source": o.get("frequency_source"),
                "due_date": o.get("due_date"),
                "boxes": o.get("boxes") or [],
                "reverse_charge": enriched_rc,
                "unclassified_sales": o.get("unclassified_sales") or 0.0,
                "notes": o.get("notes") or [],
            })

        title = f"VAT 201 draft — {period_title}" if period_title else "VAT 201 draft"
        out.append({
            "type": "vat_201",
            "title": title,
            "organizations": enriched_orgs,
            "disclaimer": DISCLAIMER,
        })

    return out
