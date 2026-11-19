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
from gemini_brain.vat_return import penalty, reconcile
from gemini_brain.vat_return import periods as tax_periods

WHICH = ("last", "current", "next_due", "period")
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


def quarter_groups(organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[int, int]:
    """{organization id: 1, 2 or 3} for organizations whose VAT settings say in which months their quarters end;
    {} when none do or the Cube has no such field. 3 is calendar quarters."""
    try:
        result = cube_client.load({"dimensions": ["vat_settings.quarter_group"], "limit": 500},
                                  organization_ids=list(organization_ids), subject=subject, deadline=deadline)
    except Exception:  # noqa: BLE001 - an older Cube: quarters are assumed, and the answer says so
        return {}
    out = {}
    for row in result.rows:
        org, value = row.get("vat_settings.organization_id"), row.get("vat_settings.quarter_group")
        try:
            group = int(float(cell_text(value)))
        except (TypeError, ValueError):
            continue
        if org is not None and group in tax_periods.QUARTER_GROUPS:
            out[int(float(org))] = group
    return out


def filing_frequencies(organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[int, str]:
    """Each organization's VAT filing frequency from its VAT settings; organizations with none are left out."""
    return _settings(organization_ids, subject, deadline)


def _figures(org: int, start: dt.date, end: dt.date, subject: str, deadline: float) -> Dict[str, float]:
    result = cube_client.load({
        "measures": ["vat.output_vat", "vat.input_vat", "vat.net_vat_payable", "vat.standard_rated_sales"],
        "timeDimensions": [{"dimension": "vat.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
        "limit": 10}, organization_ids=[org], subject=subject, deadline=deadline)
    row = result.rows[0] if result.rows else {}
    out = {k: _number(row.get(f"vat.{k}")) for k in ("output_vat", "input_vat", "net_vat_payable", "standard_rated_sales")}
    # Bills with a reverse-charge tax code: the recipient's own 5% is output VAT (Box 3) and input VAT (Box 10), as in
    # the VAT 201 draft. The net does not change. A Cube without the measure leaves the figures as they were.
    rc_vat = 0.0
    try:
        rc = cube_client.load({
            "measures": ["purchases.reverse_charge_net"],
            "timeDimensions": [{"dimension": "purchases.document_date", "dateRange": [start.isoformat(), end.isoformat()]}],
            "limit": 10}, organization_ids=[org], subject=subject, deadline=deadline)
        rc_vat = round(_number((rc.rows[0] if rc.rows else {}).get("purchases.reverse_charge_net")) * 0.05, 2)
    except Exception:  # noqa: BLE001 - the measure is extra; the return figures still stand
        rc_vat = 0.0
    out["reverse_charge_vat"] = rc_vat
    out["output_vat"] = round(out["output_vat"] + rc_vat, 2)
    out["input_vat"] = round(out["input_vat"] + rc_vat, 2)
    return out


def _ledger(org: int, start: dt.date, end: dt.date, subject: str, deadline: float) -> Optional[Dict[str, Any]]:
    """The VAT posted to the ledger's VAT accounts in the period: output VAT credited by sales invoices (source INCOME)
    and input VAT debited by bills (source EXPENSE), on asset and liability accounts named VAT. None when unavailable."""
    try:
        result = cube_client.load({
            "dimensions": ["ledger.account_name", "ledger.source_type"],
            "measures": ["ledger.debit", "ledger.credit"],
            "filters": [{"member": "ledger.account_name", "operator": "contains", "values": ["VAT"]},
                        {"member": "ledger.account_type", "operator": "equals", "values": ["Asset", "Liability"]}],
            "timeDimensions": [{"dimension": "ledger.transaction_date", "dateRange": [start.isoformat(), end.isoformat()]}],
            "limit": 200}, organization_ids=[org], subject=subject, deadline=deadline)
    except Exception:  # noqa: BLE001 - the check is extra; the return figures still stand
        return None
    output = input_ = 0.0
    accounts = set()
    for row in result.rows:
        source = str(row.get("ledger.source_type") or "").upper()
        debit, credit = _number(row.get("ledger.debit")), _number(row.get("ledger.credit"))
        accounts.add(str(row.get("ledger.account_name") or ""))
        if source == "INCOME":
            output += credit - debit
        elif source == "EXPENSE":
            input_ += debit - credit
    return {"output_vat_posted_by_sales": round(output, 2), "input_vat_posted_by_bills": round(input_, 2),
            "accounts": sorted(a for a in accounts if a)}


def _date(value: Any, name: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ToolInputError(f"{name} must be a date (YYYY-MM-DD)") from None


def resolve_period(params: Dict[str, Any], org: int, today: dt.date, known: Dict[int, str],
                   group: Optional[int], groups: Optional[Dict[int, int]] = None) -> Dict[str, Any]:
    """{"frequency", "frequency_source", "quarter_source", "start", "end", "is_tax_period", "due"} for one
    organization. `groups`: each organization's quarter group from its VAT settings (quarter_groups)."""
    forced = params.get("frequency")
    frequency = forced or known.get(org) or "quarterly"
    source = ("as asked" if forced else "from the organization's VAT settings" if org in known else
              "assumed: the books do not say; the FTA sets it on the registration certificate")
    known_group = (groups or {}).get(org)
    quarter_group = group or known_group or tax_periods.DEFAULT_QUARTER_GROUP
    quarter_source = (None if frequency == "monthly" else "as asked" if group else
                      "from the organization's VAT settings" if known_group else
                      "assumed: calendar quarters, because the VAT settings don't say in which months the quarters end")
    which = params.get("which") or "last"
    if which == "period":
        period = params.get("period") or {}
        start, end = _date(period.get("start"), "period.start"), _date(period.get("end"), "period.end")
        if start > end:
            raise ToolInputError("period start is after its end")
    elif which == "current":
        start, end = tax_periods.period_containing(today, frequency, quarter_group)
    elif which == "next_due":
        start, end = tax_periods.next_due_period(today, frequency, quarter_group)
    else:
        start, end = tax_periods.last_complete_period(today, frequency, quarter_group)
    is_tax_period = tax_periods.period_containing(start, frequency, quarter_group) == (start, end)
    due = tax_periods.due_date(end) if is_tax_period else None
    quarter_months = (None if frequency == "monthly" else
                      ("calendar quarters (March, June, September, December)" if quarter_group == 3 else
                       f"quarters ending in month {quarter_group} and every three months after"))
    return {
        "frequency": frequency, "frequency_source": source, "start": start, "end": end,
        "is_tax_period": is_tax_period, "due": due, "quarter_group": quarter_group, "quarter_source": quarter_source,
        "quarter_months": quarter_months,
    }


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
    groups = quarter_groups(organization_ids, subject, deadline) if group is None else {}

    out_orgs: List[Dict[str, Any]] = []
    notes: List[str] = [NOT_A_RETURN]
    for org in organization_ids:
        pinfo = resolve_period(params, org, today, known, group, groups)
        frequency = pinfo["frequency"]
        source = pinfo["frequency_source"]
        quarter_group = pinfo["quarter_group"]
        start, end = pinfo["start"], pinfo["end"]
        is_tax_period = pinfo["is_tax_period"]
        due = pinfo["due"]
        figures = _figures(org, start, end, subject, deadline)
        entry: Dict[str, Any] = {
            "organization_id": org, "frequency": frequency, "frequency_source": source,
            "quarter_source": pinfo["quarter_source"],
            "quarter_months": pinfo["quarter_months"],
            "period": {"start": start.isoformat(), "end": end.isoformat()}, "is_tax_period": is_tax_period, **figures,
            "position": "payable" if figures["net_vat_payable"] > 0 else "refundable" if figures["net_vat_payable"] < 0 else "nil",
            "due_date": due.isoformat() if due else None, "days_to_due": (due - today).days if due else None,
        }
        if not is_tax_period:
            n_start, n_end = tax_periods.next_due_period(today, frequency, quarter_group)
            n_due = tax_periods.due_date(n_end)
            entry["next_return"] = {"period": {"start": n_start.isoformat(), "end": n_end.isoformat()},
                                    "due_date": n_due.isoformat(), "days_to_due": (n_due - today).days}
        if due and today > due and figures["net_vat_payable"] > 0:
            entry["if_still_unfiled_and_unpaid_today"] = {
                "late_return": penalty.late_return(False), "late_payment": penalty.late_payment(figures["net_vat_payable"], due, today)}
        ledger = _ledger(org, start, end, subject, deadline)
        if ledger is not None:
            entry["ledger_vat"] = ledger
            if figures.get("reverse_charge_vat"):
                # Like for like: the VAT posted by sales and bills carries no reverse charge (it has its own journal).
                ledger["compare_with"] = {
                    "output_vat_without_reverse_charge": round(figures["output_vat"] - figures["reverse_charge_vat"], 2),
                    "input_vat_without_reverse_charge": round(figures["input_vat"] - figures["reverse_charge_vat"], 2),
                    "note": "The VAT posted by sales and bills excludes reverse charge, which is posted by its own "
                            "journal. Compare it with these figures, not with output_vat and input_vat; equal means "
                            "the ledger agrees."}
            rc_vat = figures.get("reverse_charge_vat") or 0.0  # posted by its own journal, not by the sales or bills
            if (abs(ledger["output_vat_posted_by_sales"] - (figures["output_vat"] - rc_vat)) > 1
                    or abs(ledger["input_vat_posted_by_bills"] - (figures["input_vat"] - rc_vat)) > 1):
                notes.append(
                    f"Organization {org}: the ledger's VAT accounts do not match the VAT on the documents. Sales invoices "
                    f"posted {ledger['output_vat_posted_by_sales']:,.2f} of output VAT and bills "
                    f"{ledger['input_vat_posted_by_bills']:,.2f} of input VAT, against {figures['output_vat'] - rc_vat:,.2f} and "
                    f"{figures['input_vat'] - rc_vat:,.2f} on the documents. Say both; do not call the position nil or final.")
                sides = [s for s, ledger_key, doc_key in (("output", "output_vat_posted_by_sales", "output_vat"),
                                                          ("input", "input_vat_posted_by_bills", "input_vat"))
                         if abs(ledger[ledger_key] - figures[doc_key]) > 1]
                why = reconcile.explain(org, start.isoformat(), end.isoformat(), subject, deadline, sides)
                if why:
                    entry["ledger_gap_explained"] = why
                    notes.append(
                        f"Organization {org}: ledger_gap_explained matches the ledger's VAT journals to the documents "
                        "one by one. Explain the gap with these causes only (counts and amounts as given, effect_on_gap "
                        "is ledger minus documents). Do not offer other causes, and do not name a cause that is not "
                        "listed. If unexplained is not 0, say that part is unexplained.")
        if figures.get("reverse_charge_vat"):
            notes.append(f"Organization {org}: output and input VAT each include {figures['reverse_charge_vat']:,.2f} of "
                         "reverse charge on bills with a reverse-charge tax code (Box 3 and Box 10); it does not change "
                         "the net.")
        out_orgs.append(entry)
    if any(not o["is_tax_period"] for o in out_orgs):
        notes.append("The dates asked are not a tax period, so they have no due date of their own. The due date to give "
                     "is next_return's.")
    assumed_q = [o["organization_id"] for o in out_orgs if str(o.get("quarter_source") or "").startswith("assumed")]
    if assumed_q:
        notes.append(f"Organizations {assumed_q}: calendar quarters (March, June, September, December) were assumed, "
                     "because the VAT settings don't say in which months the quarters end. Say so in the answer. If "
                     "the organization's quarters end in January or February, ask again with that quarter group.")
    rows = [{"organization_id": o["organization_id"], "period_start": o["period"]["start"], "period_end": o["period"]["end"],
             "output_vat": o["output_vat"], "input_vat": o["input_vat"], "net_vat_payable": o["net_vat_payable"],
             "due_date": o["due_date"] or (o.get("next_return") or {}).get("due_date")} for o in out_orgs]
    gap_causes = [{"organization_id": o["organization_id"], "side": side, "cause": c["cause"], "documents": c["documents"],
                   "effect_on_gap_ledger_minus_documents": c["effect_on_gap"], "examples": ", ".join(c["examples"])}
                  for o in out_orgs for side, v in (o.get("ledger_gap_explained") or {}).items() for c in v["causes"]]
    gap_causes += [{"organization_id": o["organization_id"], "side": side, "cause": "Unexplained remainder",
                    "documents": 0, "effect_on_gap_ledger_minus_documents": v["unexplained"], "examples": ""}
                   for o in out_orgs for side, v in (o.get("ledger_gap_explained") or {}).items() if abs(v["unexplained"]) > 0.01]
    result = {"today": today.isoformat(), "organizations": out_orgs, "rows": rows, "row_count": len(rows), "notes": notes}
    if gap_causes:
        result["gap_causes"] = gap_causes
    return result


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
