"""form201: drafts the UAE VAT 201 return, box by box, from the books.

Each box reports its net and VAT figures and the status of the figure:
  from_books: grounded in the sales lines or tax codes
  approximate: input VAT from bills (which carry no tax code), or unconfirmed RCM
  incomplete: the scan hit limits or timed out
  candidates_only: candidate transactions listed separately, not added to the box
  needs_emirate: standard sales could not be assigned to an emirate box (1a–1g)
  not_in_books: not tracked in the books (customs, tourist refunds)
  unavailable: the books could not be read for this box (a Cube error); no figure is shown

Never a filed return; always a draft from the books.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import cube_client
from gemini_brain.semantic import periods as semantic_periods
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.query_metrics import cell_text
from gemini_brain.vat_return import periods as tax_periods
from gemini_brain.vat_return import tool as vat_return_tool
from gemini_brain.vat_review import anomalies, duplicates
from gemini_brain.vat_review.scan import scan_bills

WHICH = ("last", "current", "next_due", "period")

DISCLAIMER = (
    "This is a draft prepared by Accutax from the books. It is not a filed return and not an FTA document. "
    "Check every figure against original documents before filing in EmaraTax."
)

EMIRATE_BOX = {
    "abu dhabi": "1a",
    "dubai": "1b",
    "sharjah": "1c",
    "ajman": "1d",
    "umm al quwain": "1e",
    "ras al khaimah": "1f",
    "fujairah": "1g",
}

BOX_1_LABELS = {
    "1a": "Standard-rated supplies in Abu Dhabi",
    "1b": "Standard-rated supplies in Dubai",
    "1c": "Standard-rated supplies in Sharjah",
    "1d": "Standard-rated supplies in Ajman",
    "1e": "Standard-rated supplies in Umm Al Quwain",
    "1f": "Standard-rated supplies in Ras Al Khaimah",
    "1g": "Standard-rated supplies in Fujairah",
    "unassigned": "Standard-rated supplies (unassigned emirate)",
    "1": "Standard-rated supplies (no sales in the period)",
}

BOX_LABELS = {
    "2": "Tax refunds provided to tourists",
    "3": "Supplies subject to the reverse charge provisions",
    "4": "Zero-rated supplies",
    "5": "Exempt supplies",
    "6": "Goods imported into the UAE",
    "7": "Adjustments to goods imported into the UAE",
    "8": "Totals",
    "9": "Standard-rated expenses",
    "10": "Supplies subject to the reverse charge provisions",
    "11": "Totals",
    "12": "Total value of due tax for the period",
    "13": "Total value of recoverable tax for the period",
    "14": "Payable tax for the period",
}

ROW_COLUMNS = ("organization_id", "box", "label", "net", "vat", "status")


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(cell_text(value))
    except (TypeError, ValueError):
        return None


#: Statuses from weakest to strongest; a total takes the weakest status of the boxes it adds.
_STRENGTH = ("unavailable", "incomplete", "needs_emirate", "approximate", "candidates_only", "from_books")


def _weakest(*statuses: str) -> str:
    for s in _STRENGTH:
        if s in statuses:
            return s
    return "from_books"


def _vat(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str,
        deadline: Optional[float] = None) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    which = params.get("which") or "last"
    if which not in WHICH:
        raise ToolInputError(f"which must be one of {list(WHICH)}")
    group = params.get("quarter_group")
    if group is not None and group not in tax_periods.QUARTER_GROUPS:
        raise ToolInputError("quarter_group is 1, 2 or 3: the month in which the first quarter of the year ends")
    forced = params.get("frequency")
    if forced is not None and forced not in tax_periods.FREQUENCIES:
        raise ToolInputError(f"frequency must be one of {list(tax_periods.FREQUENCIES)}")

    deadline_val = deadline if deadline is not None else time.monotonic() + 30.0
    today = semantic_periods.today_in(settings.report_timezone)
    known = vat_return_tool.filing_frequencies(organization_ids, subject, deadline_val) if forced is None else {}
    # Ask Cube only for the members it has: a Cube without the supplier's TRN or country still drafts the return
    # (without reverse-charge candidates), as the VAT review does.
    from gemini_brain.vat_review import tool as review_tool
    with_trn = review_tool._has_trn(organization_ids, subject, deadline_val, "purchases")
    with_country = review_tool._has_country(organization_ids, subject, deadline_val)
    with_rc = review_tool._has_rc(organization_ids, subject, deadline_val)

    out_orgs: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []

    for org in organization_ids:
        pinfo = vat_return_tool.resolve_period(params, org, today, known, group)
        start, end = pinfo["start"], pinfo["end"]
        frequency = pinfo["frequency"]
        frequency_source = pinfo["frequency_source"]
        due = pinfo["due"]

        org_notes: List[str] = []

        # 1. Sales by VAT category. A failed query is shown as unavailable, never as zero sales.
        sales_ok = True
        try:
            sales_result = cube_client.load({
                "dimensions": ["sales.vat_category", "sales.emirate"],
                "measures": ["sales.net_sales", "sales.output_vat"],
                "timeDimensions": [{"dimension": "sales.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
                "limit": 500,
            }, organization_ids=[org], subject=subject, deadline=deadline_val)
            sales_rows = sales_result.rows
        except Exception as e:  # noqa: BLE001 - CubeError, a timeout, or a Cube without the new members
            sales_ok, sales_rows = False, []
            org_notes.append(f"Organization {org}: the sales figures could not be read from the books ({type(e).__name__}), "
                             "so Boxes 1, 4, 5, 8, 12 and 14 are not shown. Try again later; do not read them as zero.")

        org_emirate_raw: Optional[str] = None
        for r in sales_rows:
            em = r.get("sales.emirate")
            if em is not None and str(em).strip():
                org_emirate_raw = str(em).strip()
                break

        if not sales_ok:
            box_1_id, box_1_status = "1", "unavailable"
        elif not sales_rows:
            box_1_id, box_1_status = "1", "from_books"  # no sales in the period: nothing to assign to an emirate
        elif org_emirate_raw and org_emirate_raw.lower() in EMIRATE_BOX:
            box_1_id, box_1_status = EMIRATE_BOX[org_emirate_raw.lower()], "from_books"
        else:
            box_1_id, box_1_status = "unassigned", "needs_emirate"

        standard_net = standard_vat = zero_rated_net = exempt_net = unclassified_sales = 0.0
        for r in sales_rows:
            cat = str(r.get("sales.vat_category") or "").strip().lower()
            net = _num(r.get("sales.net_sales")) or 0.0
            vat = _num(r.get("sales.output_vat")) or 0.0
            if cat == "standard":
                standard_net += net
                standard_vat += vat
            elif cat in ("zero_rated", "zero-rated"):
                zero_rated_net += net
            elif cat == "exempt":
                exempt_net += net
            elif cat in ("out_of_scope", "out-of-scope", "no_vat"):
                continue
            elif abs(vat) >= 0.005:  # unknown or unexpected category with VAT: standard-rated
                standard_net += net
                standard_vat += vat
            else:
                unclassified_sales += net

        if box_1_status == "needs_emirate":
            org_notes.append(
                f"Organization {org}: the organization's emirate '{org_emirate_raw or ''}' is not one of the 7 UAE "
                "emirates. Standard-rated sales cannot be assigned to an emirate box."
            )
        if abs(unclassified_sales) > 0.005:
            org_notes.append(
                f"Organization {org}: sales lines with no tax code and no VAT ({unclassified_sales:,.2f}) are in no box: "
                "zero-rated, exempt or out of scope can't be told apart."
            )

        # 2. Bills: Box 9 and the reverse-charge candidates, from the same bills the VAT review looks at.
        bills_ok = True
        try:
            scan = scan_bills(org, start, end, subject=subject, deadline=deadline_val, with_trn=with_trn,
                              with_country=with_country, side="purchases", with_rc=with_rc)
        except Exception as e:  # noqa: BLE001 - shown as unavailable, never as zero input VAT
            bills_ok, scan = False, None
            org_notes.append(f"Organization {org}: the bills could not be read from the books ({type(e).__name__}), "
                             "so Boxes 9, 11, 13 and 14 are not shown. Try again later; do not read them as zero.")
        if not with_country:
            org_notes.append(f"Organization {org}: the supplier's country is not available yet, so bills that may need "
                             "reverse charge are not listed.")
        bills, _ = duplicates.usable(scan.bills) if bills_ok else ([], 0)
        findings, _ = anomalies.find_anomalies(bills, today, side="purchases")
        foreign_vat_ids = {(f["bill"].get("document_id"), f["bill"].get("document_number"))
                           for f in findings if f.get("rule") == "foreign_vat_charged"}

        box9_net = box9_vat = rc_net = rc_bills = 0.0
        for b in bills:
            b_rc_net = _num(b.get("reverse_charge_net")) or 0.0
            b_rc_vat = _num(b.get("reverse_charge_vat_recorded")) or 0.0
            # Lines with a reverse-charge tax code: already reverse-charged in the books, Box 3 and Box 10.
            if abs(b_rc_net) >= 0.005:
                rc_net += b_rc_net
                rc_bills += 1
            b_vat = (_num(b.get("input_vat")) or 0.0) - b_rc_vat
            b_net = (_num(b.get("net_purchases")) or 0.0) - b_rc_net
            # Bills with VAT, and vendor credits that take VAT back (negative VAT), less foreign VAT.
            if abs(b_vat) >= 0.005 and (b.get("document_id"), b.get("document_number")) not in foreign_vat_ids:
                box9_net += b_net
                box9_vat += b_vat
        rc_vat = round(rc_net * 0.05, 2)
        if rc_bills:
            org_notes.append(f"Organization {org}: {int(rc_bills)} bills carry a reverse-charge tax code (net "
                             f"{rc_net:,.2f}): they are in Box 3 and Box 10 at 5%, assuming the VAT is fully recoverable. "
                             "If any of it is blocked (Executive Regulation Article 53), Box 10 is lower.")

        if not bills_ok:
            box9_status = "unavailable"
        elif not scan.complete:
            box9_status = "incomplete"
            org_notes.append(f"Organization {org}: Box 9 is incomplete: {scan.why_incomplete}.")
        else:
            box9_status = "approximate"

        rcm_candidates = []
        for f in anomalies.rcm_candidates(bills):
            b, d = f["bill"], f["details"]
            rcm_candidates.append({
                "document_number": b.get("document_number"),
                "document_id": b.get("document_id"),
                "document_date": str(b.get("document_date") or "")[:10],
                "vendor_name": b.get("vendor_name"),
                "country": d["country"],
                "net": round(d["net"], 2),
                "self_assessed_vat": d["self_assessed_vat"],
            })

        candidate_count = len(rcm_candidates)
        c_net = round(sum(c["net"] for c in rcm_candidates), 2)
        c_vat = round(sum(c["self_assessed_vat"] for c in rcm_candidates), 2)
        reverse_charge = {
            "candidates": rcm_candidates,
            "candidate_count": candidate_count,
            "if_confirmed": {
                "box3_net": c_net,
                "box3_vat": c_vat,
                "box10_net": c_net,
                "box10_vat": c_vat,
                "net_change": 0.0,
            },
            "note": (
                "These bills are not in Box 3 or Box 10 yet. Each needs a person to confirm it is an imported service "
                "or goods for the business. If the VAT is blocked (Executive Regulation Article 53), Box 10 is lower "
                "and the net change is not zero."
            ),
        }

        # 3. Boxes
        sales_status = "from_books" if sales_ok else "unavailable"
        box1 = {
            "box": box_1_id,
            "label": BOX_1_LABELS.get(box_1_id, "Standard-rated supplies"),
            "net": round(standard_net, 2) if sales_ok else None,
            "vat": round(standard_vat, 2) if sales_ok else None,
            "status": box_1_status,
            "source": "Sales lines with a standard tax code",
        }
        box2 = {"box": "2", "label": BOX_LABELS["2"], "net": None, "vat": None, "status": "not_in_books"}
        rc_status = "unavailable" if not bills_ok else "from_books" if with_rc else "candidates_only"
        box3 = {"box": "3", "label": BOX_LABELS["3"], "net": round(rc_net, 2) if bills_ok else None,
                "vat": rc_vat if bills_ok else None, "status": rc_status,
                "source": "Bill lines with a reverse-charge tax code; candidates are listed separately"}
        box4 = {"box": "4", "label": BOX_LABELS["4"], "net": round(zero_rated_net, 2) if sales_ok else None,
                "vat": None, "status": sales_status}
        box5 = {"box": "5", "label": BOX_LABELS["5"], "net": round(exempt_net, 2) if sales_ok else None,
                "vat": None, "status": sales_status}
        box6 = {"box": "6", "label": BOX_LABELS["6"], "net": None, "vat": None, "status": "not_in_books",
                "note": "Box 6 is pre-filled by the FTA from customs data. Check it in EmaraTax."}
        box7 = {"box": "7", "label": BOX_LABELS["7"], "net": None, "vat": None, "status": "not_in_books",
                "note": "Adjustments to Box 6 are not in the books. Enter them in EmaraTax if the customs figures "
                        "need correcting."}
        box8_status = _weakest(box_1_status, "approximate" if candidate_count else "from_books",
                               "unavailable" if not bills_ok else "from_books")
        box8 = {"box": "8", "label": BOX_LABELS["8"], "net": None,
                "vat": round(standard_vat + rc_vat, 2) if sales_ok else None, "status": box8_status}
        box9 = {"box": "9", "label": BOX_LABELS["9"], "net": round(box9_net, 2) if bills_ok else None,
                "vat": round(box9_vat, 2) if bills_ok else None, "status": box9_status}
        box10 = {"box": "10", "label": BOX_LABELS["10"], "net": round(rc_net, 2) if bills_ok else None,
                 "vat": rc_vat if bills_ok else None, "status": rc_status if bills_ok else "unavailable"}
        box11 = {"box": "11", "label": BOX_LABELS["11"], "net": None,
                 "vat": None if box9["vat"] is None else round(box9["vat"] + rc_vat, 2), "status": box9_status}
        box12 = {"box": "12", "label": BOX_LABELS["12"], "net": None, "vat": _vat(box8["vat"]), "status": box8_status}
        box13 = {"box": "13", "label": BOX_LABELS["13"], "net": None, "vat": _vat(box11["vat"]),
                 "status": box11["status"]}
        box14 = {"box": "14", "label": BOX_LABELS["14"], "net": None,
                 "vat": None if box12["vat"] is None or box13["vat"] is None else round(box12["vat"] - box13["vat"], 2),
                 "status": _weakest(box12["status"], box13["status"])}

        boxes = [box1, box2, box3, box4, box5, box6, box7, box8, box9, box10, box11, box12, box13, box14]

        org_entry = {
            "organization_id": org,
            "emirate": org_emirate_raw,
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "frequency": frequency,
            "frequency_source": frequency_source,
            "due_date": due.isoformat() if due else None,
            "boxes": boxes,
            "reverse_charge": reverse_charge,
            "unclassified_sales": round(unclassified_sales, 2),
            "notes": org_notes,
        }
        out_orgs.append(org_entry)

        for b in boxes:
            all_rows.append({
                "organization_id": org,
                "box": b["box"],
                "label": b["label"],
                "net": b.get("net"),
                "vat": b.get("vat"),
                "status": b.get("status"),
            })

    top_notes = [DISCLAIMER]
    for o in out_orgs:
        for n in o["notes"]:
            if n not in top_notes:
                top_notes.append(n)

    return {
        "today": today.isoformat(),
        "organizations": out_orgs,
        "rows": all_rows,
        "row_count": len(all_rows),
        "notes": top_notes,
    }


def tool_spec() -> Dict[str, Any]:
    return {"toolSpec": {
        "name": "vat_201_draft",
        "description": (
            "A draft of the UAE VAT 201 return, box by box, for the organizations selected in this chat and a tax "
            "period, from the books. Each box says whether its figure comes from the books, is approximate, or is "
            "not in the books. Lists bills that may need reverse charge (Box 3 and Box 10) without adding them. "
            "Use for 'draft our VAT return', 'VAT 201', 'fill the VAT return boxes', 'what goes in Box 3'. "
            "Never a filed return."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "which": {"type": "string", "enum": list(WHICH),
                          "description": "last: the latest ended tax period (default); current: the one running now; "
                                         "next_due: the period whose return is due next (for 'next VAT deadline'; the "
                                         "last period while its due date has not passed); period: the dates given"},
                "period": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}},
                "frequency": {"type": "string", "enum": list(tax_periods.FREQUENCIES),
                              "description": "Only when the user says how often they file."},
                "quarter_group": {"type": "integer", "enum": list(tax_periods.QUARTER_GROUPS),
                                  "description": "The month (1, 2 or 3) in which the user's first quarter of the year ends."},
            },
            "required": [],
        }},
    }}
