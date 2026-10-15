"""Every bill of one organization in a period, whatever its size.

Cube returns at most 500 rows for one query, so the period is cut in halves until each part returns fewer than 500.
A scan that runs out of time returns what it has and says so; it never pretends to be complete.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gemini_brain.semantic import cube_client

CUBE_ROW_LIMIT = 500
COLUMNS = ("document_number", "document_id", "document_date", "document_type", "status", "vendor_name")
MEASURES = ("net_purchases", "input_vat")
#: Most bills one scan keeps: a guard for memory and time, not a rule of the review.
MAX_BILLS = 50_000


@dataclass
class Scan:
    bills: List[Dict[str, Any]] = field(default_factory=list)
    complete: bool = True
    queries: int = 0
    why_incomplete: Optional[str] = None


def _query(start: dt.date, end: dt.date) -> Dict[str, Any]:
    return {
        "dimensions": [f"purchases.{c}" for c in COLUMNS] + ["purchases.currency", "purchases.organization_name"],
        "measures": [f"purchases.{m}" for m in MEASURES],
        "limit": CUBE_ROW_LIMIT,
        "timeDimensions": [{"dimension": "purchases.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
    }


def _row(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {k.split(".", 1)[1]: v for k, v in raw.items()}
    for key in MEASURES:
        try:
            out[key] = float(out[key]) if out.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            out[key] = None
    out["document_date"] = str(out.get("document_date") or "")[:10]
    return out


def scan_bills(organization_id: int, start: dt.date, end: dt.date, *, subject: str, deadline: float) -> Scan:
    scan = Scan()

    def part(a: dt.date, b: dt.date) -> None:
        if not scan.complete:
            return
        if time.monotonic() >= deadline:
            scan.complete, scan.why_incomplete = False, "time ran out"
            return
        result = cube_client.load(_query(a, b), organization_ids=[organization_id], subject=subject, deadline=deadline)
        scan.queries += 1
        if len(result.rows) < CUBE_ROW_LIMIT or a >= b:
            scan.bills.extend(_row(r) for r in result.rows)
            if len(result.rows) >= CUBE_ROW_LIMIT:  # one day with 500 or more bills: nothing finer to cut
                scan.complete, scan.why_incomplete = False, f"{a.isoformat()} has more bills than one query returns"
            if len(scan.bills) > MAX_BILLS:
                scan.complete, scan.why_incomplete = False, "too many bills"
            return
        middle = a + (b - a) // 2
        part(a, middle)
        part(middle + dt.timedelta(days=1), b)

    part(start, end)
    return scan
