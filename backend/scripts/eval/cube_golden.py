"""
cube_golden.py — Golden tool calls through query_metrics and Cube (guide section 11.4).

Each case is a query_metrics call the Phase 2 agent would make. Its rows are
checked against SQL written here, apart from the model, or the call must be
refused with a ToolInputError. Run where Cube is reachable (the VM):

    python scripts/eval/cube_golden.py --orgs 24-33

Read-only. Exits non-zero when any case fails.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gemini_brain.semantic import query_metrics  # noqa: E402
from gemini_brain.semantic.catalog import ToolInputError, clear_cache  # noqa: E402
from gemini_brain.sql_fallback.db_connection import close_pools, get_connection  # noqa: E402

TOL = Decimal("0.01")
TODAY = dt.datetime.now(ZoneInfo("Asia/Dubai")).date()
YTD = (dt.date(TODAY.year, 1, 1), TODAY)
_last_month_end = TODAY.replace(day=1) - dt.timedelta(days=1)
LAST_MONTH = (_last_month_end.replace(day=1), _last_month_end)
_q = dt.date(TODAY.year, 3 * ((TODAY.month - 1) // 3) + 1, 1)
THIS_QUARTER = (_q, TODAY)
Y2025 = (dt.date(2025, 1, 1), dt.date(2025, 12, 31))

_GL = """FROM journal_entry_lines l JOIN journal_entries j ON j.id = l.journal_entry_id
         JOIN chart_of_accounts c ON c.id = l.account_id AND c.organization_id = j.organization_id
         WHERE j.is_posted AND j.organization_id = ANY(%(orgs)s)"""
_PNL = f"""SELECT j.organization_id,
       SUM(CASE WHEN c.account_type = 'Revenue' THEN l.credit_amount - l.debit_amount ELSE 0 END) AS rev,
       SUM(CASE WHEN c.account_type = 'Expense' AND LOWER(COALESCE(c.account_sub_type, '')) = 'cost of sales'
                THEN l.debit_amount - l.credit_amount ELSE 0 END) AS cogs,
       SUM(CASE WHEN c.account_type = 'Expense' THEN l.debit_amount - l.credit_amount ELSE 0 END) AS exp
       {_GL} AND j.transaction_date BETWEEN %(start)s AND %(end)s GROUP BY 1"""
_SAFE = "'^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"


def _pnl(cur, orgs, window) -> Dict[int, Tuple[Decimal, Decimal, Decimal]]:
    cur.execute(_PNL, {"orgs": orgs, "start": window[0], "end": window[1]})
    return {int(o): (Decimal(r), Decimal(c), Decimal(e)) for o, r, c, e in cur.fetchall()}


# Each expected() returns {key tuple: Decimal}; keys match the rows' (org, group...) values.

def exp_margin_above_20(cur, orgs):
    return {(o,): Decimal(1) for o, (r, _c, e) in _pnl(cur, orgs, YTD).items() if r > 0 and 100 * (r - e) / r > 20}


def exp_loss_makers(cur, orgs):
    return {(o,): Decimal(1) for o, (r, _c, e) in _pnl(cur, orgs, YTD).items() if r - e < 0}


def exp_revenue_last_month(cur, orgs):
    return {(o,): r for o, (r, _c, _e) in _pnl(cur, orgs, LAST_MONTH).items() if r != 0}


def exp_gross_margin_2025(cur, orgs):
    return {(o,): (100 * (r - c) / r) for o, (r, c, _e) in _pnl(cur, orgs, Y2025).items() if r > 0}


def exp_working_capital(cur, orgs):
    cur.execute(f"""SELECT j.organization_id, c.currency,
        SUM(CASE WHEN c.account_type = 'Asset' AND c.account_sub_type = 'Current Asset' THEN l.debit_amount - l.credit_amount ELSE 0 END)
      - SUM(CASE WHEN c.account_type = 'Liability' AND c.account_sub_type = 'Current Liability' THEN l.credit_amount - l.debit_amount ELSE 0 END)
        {_GL} AND j.transaction_date <= %(end)s GROUP BY 1, 2""", {"orgs": orgs, "end": TODAY})
    return {(int(o), cur_): Decimal(v) for o, cur_, v in cur.fetchall()}


def exp_cash_june(cur, orgs):
    cur.execute(f"""SELECT j.organization_id, c.currency, SUM(l.debit_amount - l.credit_amount)
        {_GL} AND c.account_type = 'Asset' AND (c.account_name ILIKE '%%cash%%' OR c.account_name ILIKE '%%bank%%')
          AND j.transaction_date <= '2026-06-30' GROUP BY 1, 2""", {"orgs": orgs})
    return {(int(o), cur_): Decimal(v) for o, cur_, v in cur.fetchall()}


def exp_top_customers(cur, orgs):
    cur.execute(f"""SELECT inc.organization_id, COALESCE(NULLIF(ct.name, ''), NULLIF(ct.organization_name, ''), 'Unknown') AS customer,
               SUM(CASE WHEN inc.income_type = 'CREDIT_NOTE' THEN -1 ELSE 1 END * ii.line_amount) AS net
        FROM income inc JOIN income_items ii ON ii.income_id = inc.id JOIN status_type st ON st.id = inc.status_type_id
        LEFT JOIN contacts ct ON ct.id = inc.contact_id AND ct.organization_id = inc.organization_id
        WHERE inc.organization_id = ANY(%(orgs)s) AND NOT inc.is_draft AND inc.voided_at IS NULL
          AND st.value NOT IN ('CANCELLED', 'VOIDED') AND inc.income_type IN ('INVOICE', 'CASH_INVOICE', 'INCOME', 'CREDIT_NOTE')
          AND inc.invoice_date ~ {_SAFE} AND LEFT(inc.invoice_date, 10)::date BETWEEN %(start)s AND %(end)s
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 5""", {"orgs": orgs, "start": YTD[0], "end": YTD[1]})
    return {(int(o), c): Decimal(v) for o, c, v in cur.fetchall()}


def exp_overdue_90(cur, orgs):
    cur.execute(f"""SELECT inc.organization_id, SUM(t.total - COALESCE(inc.amount_paid, 0))
        FROM income inc JOIN status_type st ON st.id = inc.status_type_id
        CROSS JOIN LATERAL (SELECT COALESCE(SUM(ii.line_amount + COALESCE(ii.tax_amount, 0)), 0) AS total
                            FROM income_items ii WHERE ii.income_id = inc.id) t
        WHERE inc.organization_id = ANY(%(orgs)s) AND NOT inc.is_draft AND inc.voided_at IS NULL
          AND st.value IN ('PENDING', 'PARTIALLY_PAID') AND inc.income_type IN ('INVOICE', 'CASH_INVOICE', 'INCOME')
          AND NOT (inc.invoice_date ~ {_SAFE} AND LEFT(inc.invoice_date, 10)::date > %(today)s)
          AND inc.due_date ~ {_SAFE} AND %(today)s - LEFT(inc.due_date, 10)::date > 90
        GROUP BY 1""", {"orgs": orgs, "today": TODAY})
    return {(int(o),): Decimal(v) for o, v in cur.fetchall()}


def exp_vat_quarter(cur, orgs):
    out = {}
    for sign, sql in ((1, f"""SELECT inc.organization_id, SUM(CASE WHEN inc.income_type = 'CREDIT_NOTE' THEN -1 ELSE 1 END * COALESCE(ii.tax_amount, 0))
            FROM income inc JOIN income_items ii ON ii.income_id = inc.id JOIN status_type st ON st.id = inc.status_type_id
            WHERE inc.organization_id = ANY(%(orgs)s) AND NOT inc.is_draft AND inc.voided_at IS NULL
              AND st.value NOT IN ('CANCELLED', 'VOIDED') AND inc.income_type IN ('INVOICE', 'CASH_INVOICE', 'INCOME', 'CREDIT_NOTE')
              AND inc.invoice_date ~ {_SAFE} AND LEFT(inc.invoice_date, 10)::date BETWEEN %(start)s AND %(end)s GROUP BY 1"""),
                      (-1, f"""SELECT e.organization_id, SUM(CASE WHEN e.expense_type = 'VENDOR_CREDIT' THEN -1 ELSE 1 END * COALESCE(ei.tax_amount, 0))
            FROM expense e JOIN expense_items ei ON ei.expense_id = e.id JOIN status_type st ON st.id = e.status_type_id
            WHERE e.organization_id = ANY(%(orgs)s) AND NOT e.is_draft AND e.voided_at IS NULL
              AND st.value NOT IN ('CANCELLED', 'VOIDED') AND e.expense_type IN ('BILL', 'EXPENSE', 'CASH_EXPENSE', 'VENDOR_CREDIT')
              AND COALESCE(CASE WHEN e.reception_date ~ {_SAFE} THEN LEFT(e.reception_date, 10)::date END, e.created_date::date)
                  BETWEEN %(start)s AND %(end)s GROUP BY 1""")):
        cur.execute(sql, {"orgs": orgs, "start": THIS_QUARTER[0], "end": THIS_QUARTER[1]})
        for o, v in cur.fetchall():
            out[(int(o),)] = out.get((int(o),), Decimal(0)) + sign * Decimal(v)
    return out


#: (id, tool input, key columns, value column, expected() or "refused", orgs override)
CASES: List[Tuple[str, Dict[str, Any], List[str], Optional[str], Any, Optional[List[int]]]] = [
    ("demo-01 margin above 20%", {"view": "pnl", "measures": ["pnl.net_margin_pct"],
                                  "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": ["20"]}]},
     ["pnl.organization_id"], None, exp_margin_above_20, None),
    ("demo-02 net profit below zero", {"view": "pnl", "measures": ["pnl.net_profit"],
                                       "filters": [{"member": "pnl.net_profit", "operator": "lt", "values": ["0"]}]},
     ["pnl.organization_id"], None, exp_loss_makers, None),
    ("g-03 revenue last month", {"view": "pnl", "measures": ["pnl.revenue"], "period": {"preset": "last_month"}},
     ["pnl.organization_id"], "pnl.revenue", exp_revenue_last_month, None),
    ("g-04 top 5 customers YTD", {"view": "sales", "measures": ["sales.net_sales"], "group_by": ["sales.customer_name"],
                                  "order": {"member": "sales.net_sales", "direction": "desc"}, "limit": 5},
     ["sales.organization_id", "sales.customer_name"], "sales.net_sales", exp_top_customers, None),
    ("g-05 receivables over 90 days", {"view": "receivables", "measures": ["receivables.overdue_amount"],
                                       "filters": [{"member": "receivables.aging_bucket", "operator": "equals",
                                                    "values": ["Over 90 days"]}]},
     ["receivables.organization_id"], "receivables.overdue_amount", exp_overdue_90, None),
    ("g-06 gross margin 2025", {"view": "pnl", "measures": ["pnl.gross_margin_pct"],
                                "period": {"start": "2025-01-01", "end": "2025-12-31"}},
     ["pnl.organization_id"], "pnl.gross_margin_pct", exp_gross_margin_2025, None),
    ("g-07 net VAT this quarter", {"view": "vat", "measures": ["vat.net_vat_payable"], "period": {"preset": "this_quarter"}},
     ["vat.organization_id"], "vat.net_vat_payable", exp_vat_quarter, None),
    ("g-08 cash as of 30 June 2026", {"view": "balance_sheet", "measures": ["balance_sheet.cash_and_bank"],
                                      "as_of": "2026-06-30"},
     ["balance_sheet.organization_id", "balance_sheet.account_currency"], "balance_sheet.cash_and_bank",
     exp_cash_june, None),
    ("g-09 receivables in the past", {"view": "receivables", "measures": ["receivables.outstanding"],
                                      "period": {"preset": "last_year"}}, [], None, "refused", None),
    ("g-10 EBITDA", {"view": "pnl", "measures": ["pnl.ebitda"]}, [], None, "refused", None),
    ("g-11 working capital today", {"view": "balance_sheet", "measures": ["balance_sheet.working_capital"]},
     ["balance_sheet.organization_id", "balance_sheet.account_currency"], "balance_sheet.working_capital",
     exp_working_capital, None),
    ("g-12 demo-01 for org 24 only", {"view": "pnl", "measures": ["pnl.net_margin_pct"],
                                      "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": ["20"]}]},
     ["pnl.organization_id"], None, exp_margin_above_20, [24]),
    ("g-13 organization filter refused", {"view": "pnl", "measures": ["pnl.revenue"],
                                          "filters": [{"member": "pnl.organization_id", "operator": "equals",
                                                       "values": ["99"]}]}, [], None, "refused", None),
]


def _key(row: Dict[str, Any], cols: List[str]) -> tuple:
    return tuple(int(Decimal(row[c])) if c.endswith("organization_id") else row[c] for c in cols)


def run_case(case, orgs, cur) -> Tuple[bool, str]:
    name, params, key_cols, value_col, expected, only = case
    scope = only or orgs
    try:
        out = query_metrics.run(params, organization_ids=scope, subject="golden", deadline=time.monotonic() + 50)
    except ToolInputError as e:
        return (expected == "refused"), f"refused: {str(e)[:90]}"
    if expected == "refused":
        return False, "answered, but should have been refused"
    want = expected(cur, scope)
    # A row whose figure is empty (a margin with no revenue) is not an answer.
    got = {_key(r, key_cols): (Decimal(r[value_col]) if value_col else Decimal(1))
           for r in out["rows"] if value_col is None or r.get(value_col) is not None}
    if value_col is None:
        ok = set(got) == set(want)
        return ok, f"orgs got {sorted(k[0] for k in got)} want {sorted(k[0] for k in want)}"
    bad = [(k, got.get(k), want.get(k)) for k in set(got) | set(want)
           if got.get(k) is None or want.get(k) is None or abs(got[k] - want[k]) > TOL]
    return not bad, f"{len(got)} rows" + (f"; mismatches {bad[:3]}" if bad else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgs", default="24-33")
    args = parser.parse_args()
    lo, _, hi = args.orgs.partition("-")
    orgs = list(range(int(lo), int(hi or lo) + 1))
    clear_cache()
    conn = get_connection()
    conn.set_session(readonly=True)
    failed = 0
    try:
        cur = conn.cursor()
        for case in CASES:
            t0 = time.monotonic()
            try:
                ok, detail = run_case(case, orgs, cur)
            except Exception as e:
                ok, detail = False, f"{type(e).__name__}: {str(e)[:150]}"
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {case[0]:34} {time.monotonic() - t0:5.2f}s  {detail}")
    finally:
        conn.close()
        close_pools()
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
