"""Every bill (or sales invoice) of one organization in a period, whatever its size.

Cube returns at most 500 rows for one query, so the period is cut in halves until each part returns fewer than 500.
A scan that runs out of time returns what it has and says so; it never pretends to be complete.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gemini_brain.semantic import cube_client
from gemini_brain.semantic.query_metrics import cell_text

CUBE_ROW_LIMIT = 500
COLUMNS = ("document_number", "document_id", "document_date", "document_type", "status", "vendor_name")
MEASURES = ("net_purchases", "input_vat")
#: On purchases, when the Cube has them: the net amount and VAT of bill lines with a reverse-charge tax code.
RC_MEASURES = ("reverse_charge_net", "reverse_charge_vat_recorded")
#: Where each side reads from, by the names the rules use. The rules read vendor_name, net_purchases, input_vat and
#: vendor_trn for both sides: on sales they hold the customer, the net sales, the output VAT and the customer's TRN.
SIDES = {
    "purchases": {"view": "purchases", "vendor_name": "vendor_name", "net_purchases": "net_purchases",
                  "input_vat": "input_vat", "vendor_trn": "vendor_trn",
                  "vendor_country_code": "vendor_country_code"},
    "sales": {"view": "sales", "vendor_name": "customer_name", "net_purchases": "net_sales", "input_vat": "output_vat",
              "vendor_trn": "customer_trn"},
}
#: Most bills one scan keeps: a guard for memory and time, not a rule of the review.
MAX_BILLS = 50_000


@dataclass
class Scan:
    bills: List[Dict[str, Any]] = field(default_factory=list)
    complete: bool = True
    queries: int = 0
    why_incomplete: Optional[str] = None


def member(side: str, name: str) -> str:
    """The Cube member for one of the rules' names: member("sales", "vendor_name") is "sales.customer_name"."""
    spec = SIDES[side]
    return f"{spec['view']}.{spec.get(name, name)}"


def _query(start: dt.date, end: dt.date, with_trn: bool = False, with_country: bool = False,
           side: str = "purchases", with_rc: bool = False) -> Dict[str, Any]:
    view = SIDES[side]["view"]
    extra_dims: List[str] = []
    if with_trn:
        extra_dims.append("vendor_trn")
    if with_country and side == "purchases":
        extra_dims.append("vendor_country_code")
    return {
        "dimensions": [member(side, c) for c in COLUMNS + tuple(extra_dims)]
        + [f"{view}.currency", f"{view}.organization_name"],
        "measures": [member(side, m) for m in MEASURES]
        + ([f"{view}.{m}" for m in RC_MEASURES] if with_rc and side == "purchases" else []),
        "limit": CUBE_ROW_LIMIT,
        "timeDimensions": [{"dimension": f"{view}.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
    }


def _row(raw: Dict[str, Any], side: str = "purchases") -> Dict[str, Any]:
    rename = {v: k for k, v in SIDES[side].items() if k != "view"}  # customer_name -> vendor_name, on sales
    out = {rename.get(k.split(".", 1)[1], k.split(".", 1)[1]): cell_text(v) for k, v in raw.items()}  # Decimals become text: results go to the model as JSON
    for key in MEASURES + tuple(k for k in RC_MEASURES if k in out):
        try:
            out[key] = float(out[key]) if out.get(key) not in (None, "") else None
        except (TypeError, ValueError):
            out[key] = None
    out["document_date"] = str(out.get("document_date") or "")[:10]
    return out


def scan_bills(organization_id: int, start: dt.date, end: dt.date, *, subject: str, deadline: float,
               with_trn: bool = False, with_country: bool = False, side: str = "purchases",
               with_rc: bool = False) -> Scan:
    scan = Scan()

    def part(a: dt.date, b: dt.date) -> None:
        if not scan.complete:
            return
        if time.monotonic() >= deadline:
            scan.complete, scan.why_incomplete = False, "time ran out"
            return
        result = cube_client.load(_query(a, b, with_trn, with_country, side, with_rc), organization_ids=[organization_id],
                                  subject=subject, deadline=deadline)
        scan.queries += 1
        if len(result.rows) < CUBE_ROW_LIMIT or a >= b:
            scan.bills.extend(_row(r, side) for r in result.rows)
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
