"""
cube_reconcile.py — Check Cube's P&L and balance sheet against Accutax and the ledger.

Guide section 11.3. For each organization and window:
  - pnl: Cube vs Accutax GET /report/profit-loss-with-accounts (revenue,
    cost of sales, operating expenses, net profit). That endpoint filters on
    organization_id, so it is safe to call per org.
  - balance_sheet: balance_difference must be 0 (reports seed defects, does not fix them).
  - sales, purchases, vat, receivables, payables: Cube vs SQL written here,
    separately from the model, applying the rules of guide section 4.1.

Writes a CSV of every difference above 0.01 to logs/ and exits non-zero when
any exists. Run where both Cube and the Accutax API are reachable (the VM).
The Accutax call needs a valid Accutax token in ACCUTAX_AUTH_TOKEN (environment
or .env); without one the P&L comparison is skipped for each window.

    python scripts/eval/cube_reconcile.py --orgs 24-33
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import httpx  # noqa: E402

from gemini_brain.config.settings import settings  # noqa: E402  (also loads .env into the environment)
from gemini_brain.semantic import cube_client  # noqa: E402

TOLERANCE = Decimal("0.01")
PNL_FIELDS = {  # Cube member -> path in the Accutax response "details"
    "pnl.revenue": ("revenue", "total"),
    "pnl.cost_of_sales": ("cost_of_sales", "total"),
    "pnl.operating_expenses": ("operating_expenses", "total"),
    "pnl.net_profit": ("net_profit_loss",),
}


def windows(today: dt.date) -> List[Tuple[str, str, str]]:
    out = [("ytd", f"{today.year}-01-01", today.isoformat()),
           ("last_year", f"{today.year - 1}-01-01", f"{today.year - 1}-12-31")]
    for month in range(1, today.month + 1):
        start = dt.date(today.year, month, 1)
        end = (dt.date(today.year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1))
        out.append((start.strftime("%Y-%m"), start.isoformat(), min(end, today).isoformat()))
    return out


def accutax_pnl(org: int, start: str, end: str) -> Optional[Dict[str, Decimal]]:
    token = os.getenv("ACCUTAX_AUTH_TOKEN", "")
    try:
        resp = httpx.get(settings.accutax_base_url.rstrip("/") + "/report/profit-loss-with-accounts",
                         params={"organization_id": org, "start_date": start, "end_date": end},
                         headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=60)
        ok, body = resp.status_code == 200, (resp.json() if resp.status_code == 200 else resp.text)
    except (httpx.HTTPError, ValueError) as e:
        ok, body = False, e
    if not ok or not isinstance(body, dict):
        print(f"  Accutax call failed for org {org} {start}..{end}: {str(body)[:200]}")
        return None
    details = body.get("details") or {}
    out = {}
    for member, path in PNL_FIELDS.items():
        value = details
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        out[member] = Decimal(str(value or 0))
    return out


def cube_pnl(orgs: List[int], start: str, end: str) -> Optional[Dict[int, Dict[str, Decimal]]]:
    """Cube's P&L per org; one retry, since a cold first read can exceed Cube's 20 s query timeout."""
    query = {"measures": list(PNL_FIELDS), "dimensions": ["pnl.organization_id"],
             "timeDimensions": [{"dimension": "pnl.transaction_date", "dateRange": [start, end]}]}
    for attempt in (1, 2):
        try:
            result = cube_client.load(query, organization_ids=orgs, subject="reconcile",
                                      deadline=time.monotonic() + 50)
            return {int(r["pnl.organization_id"]): {m: Decimal(str(r.get(m) or 0)) for m in PNL_FIELDS}
                    for r in result.rows}
        except cube_client.CubeError as e:
            print(f"  Cube attempt {attempt} failed for {start}..{end}: {str(e)[:160]}")
    return None


# ── Document views ───────────────────────────────────────────────────────────
# Written here, apart from semantic/model, on purpose: generated from one
# definition, a wrong rule would agree with itself. Rules: guide section 4.1
# (pending finance sign-off).

_SAFE = "'^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"


def _day(column: str, fallback: str = "NULL") -> str:
    return f"COALESCE(CASE WHEN {column} ~ {_SAFE} THEN LEFT({column}, 10)::date END, {fallback})"


_INC_DATE, _EXP_DATE = _day("inc.invoice_date"), _day("e.reception_date", "e.created_date::date")
_INC_OK = ("inc.organization_id = ANY(%(orgs)s) AND NOT inc.is_draft AND inc.voided_at IS NULL "
           "AND st.value NOT IN ('CANCELLED', 'VOIDED')")
_EXP_OK = ("e.organization_id = ANY(%(orgs)s) AND NOT e.is_draft AND e.voided_at IS NULL "
           "AND st.value NOT IN ('CANCELLED', 'VOIDED')")

#: (view, Cube members in order, SQL returning org + the same values in order, needs a period)
DOCUMENT_CHECKS = [
    ("sales", ["sales.gross_sales", "sales.credit_notes", "sales.output_vat", "sales.invoice_count"], f"""
        SELECT inc.organization_id,
               SUM(ii.line_amount) FILTER (WHERE inc.income_type <> 'CREDIT_NOTE'),
               SUM(ii.line_amount) FILTER (WHERE inc.income_type = 'CREDIT_NOTE'),
               SUM(CASE WHEN inc.income_type = 'CREDIT_NOTE' THEN -1 ELSE 1 END * COALESCE(ii.tax_amount, 0)),
               COUNT(DISTINCT inc.id) FILTER (WHERE inc.income_type <> 'CREDIT_NOTE')
        FROM income inc JOIN income_items ii ON ii.income_id = inc.id JOIN status_type st ON st.id = inc.status_type_id
        WHERE {_INC_OK} AND inc.income_type IN ('INVOICE', 'CASH_INVOICE', 'INCOME', 'CREDIT_NOTE')
          AND {_INC_DATE} BETWEEN %(start)s AND %(end)s
        GROUP BY 1""", True),
    ("purchases", ["purchases.gross_purchases", "purchases.vendor_credits", "purchases.input_vat",
                   "purchases.bill_count"], f"""
        SELECT e.organization_id,
               SUM(ei.line_amount) FILTER (WHERE e.expense_type <> 'VENDOR_CREDIT'),
               SUM(ei.line_amount) FILTER (WHERE e.expense_type = 'VENDOR_CREDIT'),
               SUM(CASE WHEN e.expense_type = 'VENDOR_CREDIT' THEN -1 ELSE 1 END * COALESCE(ei.tax_amount, 0)),
               COUNT(DISTINCT e.id) FILTER (WHERE e.expense_type <> 'VENDOR_CREDIT')
        FROM expense e JOIN expense_items ei ON ei.expense_id = e.id JOIN status_type st ON st.id = e.status_type_id
        WHERE {_EXP_OK} AND e.expense_type IN ('BILL', 'EXPENSE', 'CASH_EXPENSE', 'VENDOR_CREDIT')
          AND {_EXP_DATE} BETWEEN %(start)s AND %(end)s
        GROUP BY 1""", True),
    ("receivables", ["receivables.outstanding", "receivables.overdue_amount", "receivables.open_count",
                     "receivables.overdue_count"], f"""
        SELECT inc.organization_id,
               SUM(t.total - COALESCE(inc.amount_paid, 0)),
               SUM(t.total - COALESCE(inc.amount_paid, 0)) FILTER (WHERE {_day('inc.due_date')} < %(today)s),
               COUNT(*), COUNT(*) FILTER (WHERE {_day('inc.due_date')} < %(today)s)
        FROM income inc JOIN status_type st ON st.id = inc.status_type_id
        CROSS JOIN LATERAL (SELECT COALESCE(SUM(ii.line_amount + COALESCE(ii.tax_amount, 0)), 0) AS total
                            FROM income_items ii WHERE ii.income_id = inc.id) t
        WHERE inc.organization_id = ANY(%(orgs)s) AND NOT inc.is_draft AND inc.voided_at IS NULL
          AND st.value IN ('PENDING', 'PARTIALLY_PAID') AND inc.income_type IN ('INVOICE', 'CASH_INVOICE', 'INCOME')
          AND NOT COALESCE({_INC_DATE} > %(today)s, false)
        GROUP BY 1""", False),
    ("payables", ["payables.outstanding", "payables.overdue_amount", "payables.open_count",
                  "payables.overdue_count"], f"""
        SELECT e.organization_id,
               SUM(t.total - COALESCE(e.amount_paid, 0)),
               SUM(t.total - COALESCE(e.amount_paid, 0)) FILTER (WHERE {_day('e.due_date')} < %(today)s),
               COUNT(*), COUNT(*) FILTER (WHERE {_day('e.due_date')} < %(today)s)
        FROM expense e JOIN status_type st ON st.id = e.status_type_id
        CROSS JOIN LATERAL (SELECT COALESCE(SUM(ei.line_amount + COALESCE(ei.tax_amount, 0)), 0) AS total
                            FROM expense_items ei WHERE ei.expense_id = e.id) t
        WHERE e.organization_id = ANY(%(orgs)s) AND NOT e.is_draft AND e.voided_at IS NULL
          AND st.value IN ('PENDING', 'PARTIALLY_PAID') AND e.expense_type IN ('BILL', 'EXPENSE', 'CASH_EXPENSE')
          AND NOT COALESCE({_EXP_DATE} > %(today)s, false)
        GROUP BY 1""", False),
]
_TIME = {"sales": "sales.document_date", "purchases": "purchases.document_date"}


def _num(value: object) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def reconcile_documents(orgs: List[int], cur, today: dt.date) -> List[List[str]]:
    """Every document view's measures vs the SQL above, per org; VAT from the same sums."""
    diffs: List[List[str]] = []
    flow_windows = [("ytd", f"{today.year}-01-01", today.isoformat()),
                    ("last_year", f"{today.year - 1}-01-01", f"{today.year - 1}-12-31"),
                    ("this_month", today.replace(day=1).isoformat(), today.isoformat())]
    for view, members, sql, flow in DOCUMENT_CHECKS:
        for label, start, end in (flow_windows if flow else [("today", "", "")]):
            cur.execute(sql, {"orgs": orgs, "start": start or None, "end": end or None, "today": today})
            expected = {int(r[0]): [_num(v) for v in r[1:]] for r in cur.fetchall()}
            query = {"measures": members, "dimensions": [f"{view}.organization_id"]}
            if flow:
                query["timeDimensions"] = [{"dimension": _TIME[view], "dateRange": [start, end]}]
            try:
                rows = cube_client.load(query, organization_ids=orgs, subject="reconcile",
                                        deadline=time.monotonic() + 50).rows
            except cube_client.CubeError as e:
                diffs.append([label, "all", view, "", "", f"cube error: {str(e)[:120]}"])
                continue
            got = {int(r[f"{view}.organization_id"]): [_num(r.get(m)) for m in members] for r in rows}
            for org in orgs:
                want, mine = expected.get(org, [Decimal(0)] * len(members)), got.get(org, [Decimal(0)] * len(members))
                for member, w, g in zip(members, want, mine):
                    if abs(g - w) > TOLERANCE:
                        diffs.append([label, str(org), member, str(g), str(w), str(g - w)])
    # The vat view: output VAT by the sales rules, input VAT by the purchase rules.
    for label, start, end in flow_windows:
        cur.execute(DOCUMENT_CHECKS[0][2], {"orgs": orgs, "start": start, "end": end, "today": today})
        out_vat = {int(r[0]): _num(r[3]) for r in cur.fetchall()}
        cur.execute(DOCUMENT_CHECKS[1][2], {"orgs": orgs, "start": start, "end": end, "today": today})
        in_vat = {int(r[0]): _num(r[3]) for r in cur.fetchall()}
        rows = cube_client.load(
            {"measures": ["vat.output_vat", "vat.input_vat", "vat.net_vat_payable"], "dimensions": ["vat.organization_id"],
             "timeDimensions": [{"dimension": "vat.document_date", "dateRange": [start, end]}]},
            organization_ids=orgs, subject="reconcile", deadline=time.monotonic() + 50).rows
        got = {int(r["vat.organization_id"]): r for r in rows}
        for org in orgs:
            want = {"vat.output_vat": out_vat.get(org, Decimal(0)), "vat.input_vat": in_vat.get(org, Decimal(0))}
            want["vat.net_vat_payable"] = want["vat.output_vat"] - want["vat.input_vat"]
            for member, w in want.items():
                g = _num((got.get(org) or {}).get(member))
                if abs(g - w) > TOLERANCE:
                    diffs.append([label, str(org), member, str(g), str(w), str(g - w)])
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgs", default="24-33")
    args = parser.parse_args()
    lo, _, hi = args.orgs.partition("-")
    orgs = list(range(int(lo), int(hi or lo) + 1))
    diffs: List[List[str]] = []

    from zoneinfo import ZoneInfo

    from gemini_brain.db.connection import get_connection

    conn = get_connection()
    conn.set_session(readonly=True)
    try:
        doc_diffs = reconcile_documents(orgs, conn.cursor(), dt.datetime.now(ZoneInfo("Asia/Dubai")).date())
    finally:
        conn.close()
    print(f"document views: {len(doc_diffs)} differences")
    diffs += doc_diffs

    for label, start, end in windows(dt.date.today()):
        cube = cube_pnl(orgs, start, end)
        if cube is None:
            diffs.append([label, "all", "cube_query", "", "", "failed"])
            continue
        for org in orgs:
            accutax = accutax_pnl(org, start, end)
            if accutax is None:
                diffs.append([label, str(org), "accutax_call", "", "", "failed"])
                continue
            mine = cube.get(org, {m: Decimal(0) for m in PNL_FIELDS})
            for member in PNL_FIELDS:
                delta = mine[member] - accutax[member]
                if abs(delta) > TOLERANCE:
                    diffs.append([label, str(org), member, str(mine[member]), str(accutax[member]), str(delta)])

    balance = cube_client.load(
        {"measures": ["balance_sheet.balance_difference"],
         "dimensions": ["balance_sheet.organization_id", "balance_sheet.account_currency"],
         "timeDimensions": [{"dimension": "balance_sheet.transaction_date",
                             "dateRange": ["1900-01-01", dt.date.today().isoformat()]}]},
        organization_ids=orgs, subject="reconcile", deadline=time.monotonic() + 50)
    for row in balance.rows:
        value = Decimal(str(row.get("balance_sheet.balance_difference") or 0))
        if abs(value) > TOLERANCE:
            diffs.append(["today", str(int(row["balance_sheet.organization_id"])),
                          f"balance_difference ({row.get('balance_sheet.account_currency')})", str(value), "0", str(value)])

    out = Path(__file__).resolve().parents[2] / "logs" / f"cube_reconcile_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["window", "org", "member", "cube", "expected", "difference"])
        writer.writerows(diffs)
    print(f"{len(diffs)} differences above {TOLERANCE}; written to {out}")
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
