"""
cash_forecast.py — Weekly cash projection from governed figures.

Opening cash is balance_sheet.cash_and_bank today. Open invoices are collected
and open bills are paid in the week they fall due. Each week's closing cash is
worked out here, never by the model. Overdue and undated amounts are not spread
over the weeks: they are reported beside the weeks, with what closing cash would
be if they were settled at once. Tenant scope comes from the request.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import datetime as dt
from decimal import Decimal
from typing import Any, Dict, List, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.semantic import cube_client, periods
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.query_metrics import BEGINNING_OF_TIME

DEFAULT_WEEKS, MAX_WEEKS = 13, 26
#: Cube returns at most 500 rows (cube.js MAX_ROWS); one row per organization and week.
MAX_ROWS = 480
VIEWS = ("balance_sheet", "receivables", "payables")
ASSUMPTIONS = (
    "Open invoices are assumed collected, and open bills paid, in the week they fall due.",
    "Only invoices and bills already issued are included: no payroll, rent or other spending not yet billed.",
    "Opening cash is the ledger balance of accounts named cash or bank, in the organization's currency.",
)


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    weeks = _weeks(params)
    orgs = list(organization_ids)
    if len(orgs) * (weeks + 1) > MAX_ROWS:
        raise ToolInputError(f"Too many organizations for {weeks} weeks; ask for fewer weeks or organizations")
    today = periods.today_in(settings.report_timezone)
    week_starts = [today - dt.timedelta(days=today.weekday()) + dt.timedelta(weeks=i) for i in range(weeks)]
    horizon_end = week_starts[-1] + dt.timedelta(days=6)

    queries = {"cash": _cash_query(today)}
    for side in ("receivables", "payables"):
        queries[f"{side}_totals"] = _totals_query(side)
        queries[f"{side}_undated"] = _undated_query(side)
        queries[f"{side}_weekly"] = _weekly_query(side, today, horizon_end)

    def load(query: Dict[str, Any]) -> List[Dict[str, Any]]:
        return cube_client.load(query, organization_ids=orgs, subject=subject, deadline=deadline).rows

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries), thread_name_prefix="cash-forecast") as pool:
        # Each query runs in a copy of this context, so the request's trace collectors see it.
        futures = {k: pool.submit(contextvars.copy_context().run, load, q) for k, q in queries.items()}
        rows = {k: f.result() for k, f in futures.items()}

    return {
        "weeks": weeks,
        "period": f"{week_starts[0].isoformat()} to {horizon_end.isoformat()} (weeks start on Monday; today is "
                  f"{today.isoformat()})",
        "organizations": [_forecast(o, rows, week_starts, today) for o in _org_list(orgs, rows)],
        "assumptions": list(ASSUMPTIONS),
        "notes": ["Figures are per organization, in that organization's currency. "
                  "Never add amounts across currencies."],
        "row_count": sum(len(r) for r in rows.values()),
    }


def _weeks(params: Any) -> int:
    raw = (params or {}).get("weeks") if isinstance(params, dict) else None
    try:
        weeks = DEFAULT_WEEKS if raw is None else int(raw)
    except (TypeError, ValueError):
        raise ToolInputError("weeks must be a whole number") from None
    if not 1 <= weeks <= MAX_WEEKS:
        raise ToolInputError(f"weeks must be between 1 and {MAX_WEEKS}")
    return weeks


def _org_dims(view: str) -> List[str]:
    return [f"{view}.organization_id", f"{view}.organization_name", f"{view}.currency"]


def _cash_query(today: dt.date) -> Dict[str, Any]:
    return {"measures": ["balance_sheet.cash_and_bank"],
            "dimensions": _org_dims("balance_sheet") + ["balance_sheet.account_currency"],
            "timeDimensions": [{"dimension": "balance_sheet.transaction_date",
                                "dateRange": [BEGINNING_OF_TIME, today.isoformat()]}],
            "limit": 500}


def _totals_query(side: str) -> Dict[str, Any]:
    return {"measures": [f"{side}.outstanding", f"{side}.overdue_amount"], "dimensions": _org_dims(side),
            "limit": 500}


def _undated_query(side: str) -> Dict[str, Any]:
    return {"measures": [f"{side}.outstanding"], "dimensions": _org_dims(side),
            "filters": [{"member": f"{side}.due_date", "operator": "notSet"}], "limit": 500}


def _weekly_query(side: str, today: dt.date, end: dt.date) -> Dict[str, Any]:
    return {"measures": [f"{side}.outstanding"], "dimensions": _org_dims(side),
            "timeDimensions": [{"dimension": f"{side}.due_date", "granularity": "week",
                                "dateRange": [today.isoformat(), end.isoformat()]}],
            "limit": 500}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _org_list(orgs: Sequence[int], rows: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Each organization once, with the name and currency Cube returned for it."""
    seen: Dict[int, Dict[str, Any]] = {int(o): {"id": int(o), "name": None, "currency": None} for o in orgs}
    for key, view in (("cash", "balance_sheet"), ("receivables_totals", "receivables"),
                      ("payables_totals", "payables")):
        for r in rows[key]:
            org = seen.get(int(r[f"{view}.organization_id"]))
            if org is not None:
                org["name"] = org["name"] or r.get(f"{view}.organization_name")
                org["currency"] = org["currency"] or r.get(f"{view}.currency")
    return list(seen.values())


def _sum(rows: List[Dict[str, Any]], view: str, org_id: int, measure: str) -> Decimal:
    return sum((_dec(r.get(f"{view}.{measure}")) for r in rows if int(r[f"{view}.organization_id"]) == org_id),
               Decimal(0))


def _by_week(rows: List[Dict[str, Any]], view: str, org_id: int) -> Dict[str, Decimal]:
    out: Dict[str, Decimal] = {}
    for r in rows:
        if int(r[f"{view}.organization_id"]) != org_id:
            continue
        week = str(r.get(f"{view}.due_date.week") or r.get(f"{view}.due_date") or "")[:10]
        out[week] = out.get(week, Decimal(0)) + _dec(r.get(f"{view}.outstanding"))
    return out


def _forecast(org: Dict[str, Any], rows: Dict[str, List[Dict[str, Any]]], week_starts: List[dt.date],
              today: dt.date) -> Dict[str, Any]:
    oid, currency = org["id"], org["currency"]
    opening, other_cash = Decimal(0), {}
    for r in rows["cash"]:
        if int(r["balance_sheet.organization_id"]) != oid:
            continue
        acct_currency = r.get("balance_sheet.account_currency")
        value = _dec(r.get("balance_sheet.cash_and_bank"))
        if not acct_currency or acct_currency == currency:
            opening += value
        else:
            other_cash[acct_currency] = str(other_cash.get(acct_currency, Decimal(0)) + value)

    sides: Dict[str, Dict[str, Any]] = {}
    for side in ("receivables", "payables"):
        total = _sum(rows[f"{side}_totals"], side, oid, "outstanding")
        overdue = _sum(rows[f"{side}_totals"], side, oid, "overdue_amount")
        undated = _sum(rows[f"{side}_undated"], side, oid, "outstanding")
        weekly = _by_week(rows[f"{side}_weekly"], side, oid)
        in_weeks = sum(weekly.values(), Decimal(0))
        sides[side] = {"weekly": weekly, "overdue": overdue, "undated": undated,
                       "later": total - overdue - undated - in_weeks}

    balance, table = opening, []
    for start in week_starts:
        key = start.isoformat()
        inflow = sides["receivables"]["weekly"].get(key, Decimal(0))
        outflow = sides["payables"]["weekly"].get(key, Decimal(0))
        balance += inflow - outflow
        table.append({"week_start": max(start, today).isoformat(),
                      "week_end": (start + dt.timedelta(days=6)).isoformat(),
                      "collections_due": str(inflow), "payments_due": str(outflow),
                      "net": str(inflow - outflow), "closing_cash": str(balance)})

    rec, pay = sides["receivables"], sides["payables"]
    out: Dict[str, Any] = {
        "organization_id": oid, "organization_name": org["name"], "currency": currency,
        "opening_cash": str(opening),
        "weeks": table,
        "closing_cash": str(balance),
        "lowest_closing_cash": min((row["closing_cash"] for row in table), key=Decimal, default=str(opening)),
        "overdue_receivables": str(rec["overdue"]), "overdue_payables": str(pay["overdue"]),
        "closing_cash_if_overdue_settled": str(balance + rec["overdue"] - pay["overdue"]),
        "receivables_without_due_date": str(rec["undated"]), "payables_without_due_date": str(pay["undated"]),
        "receivables_due_after_horizon": str(rec["later"]), "payables_due_after_horizon": str(pay["later"]),
    }
    if other_cash:
        out["cash_in_other_currencies"] = other_cash
    return out


def tool_spec() -> Dict[str, Any]:
    """Bedrock Converse toolSpec for cash_forecast."""
    return {"toolSpec": {
        "name": "cash_forecast",
        "description": (
            "Weekly cash projection for the organizations selected in this chat: opening cash today "
            "(ledger cash and bank), open invoices collected and open bills paid in the week they fall due, "
            "and the closing cash each week. Overdue amounts, amounts without a due date and amounts due "
            "after the horizon are reported separately, with the closing cash if overdue items were settled. "
            "Use for cash forecast, projected cash, expected collections and payments, cash runway."
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "weeks": {"type": "integer", "minimum": 1, "maximum": MAX_WEEKS,
                          "description": f"How many weeks ahead, starting this week. Default {DEFAULT_WEEKS}."},
            },
        }},
    }}


def available(view_names: Sequence[str]) -> bool:
    return all(v in view_names for v in VIEWS)
