"""vat_return and vat_penalty: the agent tools for the VAT return position, the due date and penalties.

vat_return runs on the organizations selected in the chat, like the other figure tools. For each organization it finds
the tax period (monthly or quarterly, from the VAT settings when the books have them, else assumed quarterly), adds up
the output and input VAT of the documents in that period, and gives the due date and, when the date has passed, what
the penalties would be if the return were still not filed and the tax not paid. The books hold no filed return, so it
never says whether a return was filed.

vat_penalty is a calculator: the penalty for a given amount, due date and payment date, under the current rules
(penalty.py). Neither tool lets the model do the arithmetic.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import cube_client
from gemini_brain.semantic import periods as semantic_periods
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.cube_client import CubeError
from gemini_brain.semantic.query_metrics import cell_text
from gemini_brain.vat_return import penalty
from gemini_brain.vat_return import periods as tax_periods

WHICH = ("last", "current", "period")
NOT_A_RETURN = ("These figures are the VAT on the documents in the books for the period, not a filed return: the books "
                "hold no filed returns, so whether this return was filed or paid is not known.")


def _number(value: Any) -> float:
    try:
        return round(float(cell_text(value)), 2)
    except (TypeError, ValueError):
        return 0.0


def _settings(organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[int, str]:
    """{organization id: monthly or quarterly} from the VAT settings; {} when the books have none or Cube lacks the view."""
    try:
        result = cube_client.load({"dimensions": ["vat_settings.filing_frequency"], "limit": 500},
                                  organization_ids=list(organization_ids), subject=subject, deadline=deadline)
    except (CubeError, Exception):  # noqa: BLE001 - a Cube without the view: assume quarterly, and say so
        return {}
    out = {}
    for row in result.rows:
        org = row.get("vat_settings.organization_id")
        freq = str(row.get("vat_settings.filing_frequency") or "").strip().lower()
        if org is not None and freq in tax_periods.FREQUENCIES:
            out[int(float(org))] = freq
    return out


def _figures(org: int, start: dt.date, end: dt.date, subject: str, deadline: float) -> Dict[str, float]:
    result = cube_client.load({
        "measures": ["vat.output_vat", "vat.input_vat", "vat.net_vat_payable", "vat.standard_rated_sales"],
        "timeDimensions": [{"dimension": "vat.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
        "limit": 10}, organization_ids=[org], subject=subject, deadline=deadline)
    row = result.rows[0] if result.rows else {}
    return {k: _number(row.get(f"vat.{k}")) for k in ("output_vat", "input_vat", "net_vat_payable", "standard_rated_sales")}


def _date(value: Any, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ToolInputError(f"{name} must be a date (YYYY-MM-DD)") from None


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
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
    today = semantic_periods.today_in(settings.report_timezone)
    known = _settings(organization_ids, subject, deadline) if forced is None else {}

    out_orgs: List[Dict[str, Any]] = []
    notes: List[str] = [NOT_A_RETURN]
    for org in organization_ids:
        frequency = forced or known.get(org) or "quarterly"
        source = ("as asked" if forced else "from the organization's VAT settings" if org in known else
                  "assumed: the books do not say; the FTA sets it on the registration certificate")
        quarter_group = group or tax_periods.DEFAULT_QUARTER_GROUP
        if which == "period":
            period = params.get("period") or {}
            start, end = _date(period.get("start"), "period.start"), _date(period.get("end"), "period.end")
            if start > end:
                raise ToolInputError("period start is after its end")
        elif which == "current":
            start, end = tax_periods.period_containing(today, frequency, quarter_group)
        else:
            start, end = tax_periods.last_complete_period(today, frequency, quarter_group)
        figures = _figures(org, start, end, subject, deadline)
        due = tax_periods.due_date(end)
        entry: Dict[str, Any] = {
            "organization_id": org, "frequency": frequency, "frequency_source": source,
            "quarter_months": (None if frequency == "monthly" else
                               ("calendar quarters (March, June, September, December)" if quarter_group == 3 else
                                f"quarters ending in month {quarter_group} and every three months after")),
            "period": {"start": start.isoformat(), "end": end.isoformat()}, **figures,
            "position": "payable" if figures["net_vat_payable"] > 0 else "refundable" if figures["net_vat_payable"] < 0 else "nil",
            "due_date": due.isoformat(), "days_to_due": (due - today).days,
        }
        if today > due and figures["net_vat_payable"] > 0:
            entry["if_still_unfiled_and_unpaid_today"] = {
                "late_return": penalty.late_return(False), "late_payment": penalty.late_payment(figures["net_vat_payable"], due, today)}
        out_orgs.append(entry)
    if any(o["frequency_source"].startswith("assumed") for o in out_orgs) and group is None:
        notes.append("Quarterly periods were assumed to be calendar quarters. If the organization's quarters end in "
                     "January or February, ask again with that quarter group.")
    rows = [{"organization_id": o["organization_id"], "period_start": o["period"]["start"], "period_end": o["period"]["end"],
             "output_vat": o["output_vat"], "input_vat": o["input_vat"], "net_vat_payable": o["net_vat_payable"],
             "due_date": o["due_date"]} for o in out_orgs]
    return {"today": today.isoformat(), "organizations": out_orgs, "rows": rows, "row_count": len(rows), "notes": notes}


def tool_spec() -> Dict[str, Any]:
    return {"toolSpec": {
        "name": "vat_return",
        "description": (
            "The VAT return position of the organizations selected in this chat for a tax period: output VAT, input "
            "VAT, the net payable or refundable, the due date (28 days after the period ends), the days left or "
            "overdue, and the penalties if the return were still unfiled and unpaid today. Monthly or quarterly from "
            "the VAT settings, else quarterly. Use for 'VAT return', 'how much VAT do we owe this quarter', 'when is "
            "our VAT due', 'next VAT deadline'. The figures come from the documents in the books, not a filed return."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "which": {"type": "string", "enum": list(WHICH),
                          "description": "last: the latest ended tax period (default); current: the one running now; "
                                         "period: the dates given in period"},
                "period": {"type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}},
                "frequency": {"type": "string", "enum": list(tax_periods.FREQUENCIES),
                              "description": "Only when the user says how often they file."},
                "quarter_group": {"type": "integer", "enum": list(tax_periods.QUARTER_GROUPS),
                                  "description": "The month (1, 2 or 3) in which the user's first quarter of the year ends."},
            },
            "required": [],
        }},
    }}


# ── vat_penalty ───────────────────────────────────────────────────────────────────────────────────

PENALTY_KINDS = ("late_payment", "late_return", "voluntary_disclosure")


def penalty_run(params: Dict[str, Any], today: Optional[dt.date] = None) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    kind = params.get("kind")
    if kind not in PENALTY_KINDS:
        raise ToolInputError(f"kind must be one of {list(PENALTY_KINDS)}")
    today = today or semantic_periods.today_in(settings.report_timezone)
    if kind == "late_return":
        return penalty.late_return(bool(params.get("repeated_within_24_months")))
    try:
        amount = float(params.get("amount"))
    except (TypeError, ValueError):
        raise ToolInputError("amount must be a number (AED)") from None
    due = _date(params.get("due_date"), "due_date")
    until = _date(params["paid_on"], "paid_on") if params.get("paid_on") else today
    if until < due:
        raise ToolInputError("paid_on is before due_date: no penalty")
    if kind == "late_payment":
        return penalty.late_payment(amount, due, until)
    return penalty.voluntary_disclosure(amount, due, until)


PENALTY_SPEC = {"toolSpec": {
    "name": "vat_penalty",
    "description": (
        "Calculates a UAE VAT administrative penalty under the rules in force from 14 April 2026: late_payment (14% per "
        "annum, for each month or part, on the unpaid tax), late_return (AED 1,000, or 2,000 when repeated within 24 "
        "months), voluntary_disclosure (1% of the tax difference per month or part). Give the amount in AED, the due "
        "date and the date paid (omit for today). Never calculate a penalty yourself."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(PENALTY_KINDS)},
            "amount": {"type": "number", "description": "The unpaid tax, or the tax difference of a disclosure, in AED"},
            "due_date": {"type": "string", "description": "YYYY-MM-DD: the payment due date, or the return's due date"},
            "paid_on": {"type": "string", "description": "YYYY-MM-DD: when paid or disclosed; omit for today"},
            "repeated_within_24_months": {"type": "boolean"},
        },
        "required": ["kind"],
    }},
}}
