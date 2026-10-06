"""
shadow_review.py — Put every SQL-vs-Cube shadow difference into a category.

Guide section 12.2: before Cube answers users, each difference in
logs/metrics_shadow.jsonl must be a rule change, a data defect, a Cube model
bug or an old-path bug. This reads the log and assigns, per record:

  match              |cube - sql| <= 0.01
  same_empty         one side "nothing recorded" or empty, the other 0 or empty
  basis_change       revenue / expenses / profit / margin: Cube reads the posted
                     ledger, the SQL path reads invoices and bills (decision D1)
  rule_drafts        the gap equals, to 0.01, the documents the SQL path counts
                     and the 4.1 rules exclude (drafts, voided_at set, non-sales
                     types); checked with a query here
  fixed_day_boundary receivables/payables: the gap equals open documents dated
                     exactly on the as-of day. Cube used the UTC date until
                     2026-10-07 (fixed: each organization's own time zone)
  cube_error         Cube did not answer
  unexplained        none of the above: investigate before switching

Read-only. Exits non-zero when anything is unexplained.

    python scripts/eval/shadow_review.py [--log logs/metrics_shadow.jsonl]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND / "src"))
from gemini_brain.sql_fallback.db_connection import close_pools, get_connection  # noqa: E402

TOL = Decimal("0.01")
BASIS_CHANGE = {"revenue", "expenses", "net_profit", "profit_margin"}
_SAFE = "'^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"

#: Tax on documents the SQL path counts but the 4.1 rules exclude, per org and period.
_EXCLUDED_TAX = {
    "output": f"""
        SELECT COALESCE(SUM(ii.tax_amount), 0) FROM income inc
        JOIN income_items ii ON ii.income_id = inc.id JOIN status_type st ON st.id = inc.status_type_id
        WHERE inc.organization_id = %(org)s AND st.value NOT IN ('CANCELLED', 'VOIDED') AND ii.tax_amount > 0
          AND (inc.is_draft OR inc.voided_at IS NOT NULL
               OR inc.income_type NOT IN ('INVOICE', 'CASH_INVOICE', 'INCOME', 'CREDIT_NOTE'))
          AND inc.invoice_date ~ {_SAFE} AND LEFT(inc.invoice_date, 10)::date BETWEEN %(start)s AND %(end)s""",
    "input": f"""
        SELECT COALESCE(SUM(ei.tax_amount), 0) FROM expense e
        JOIN expense_items ei ON ei.expense_id = e.id JOIN status_type st ON st.id = e.status_type_id
        WHERE e.organization_id = %(org)s AND st.value NOT IN ('CANCELLED', 'VOIDED') AND ei.tax_amount > 0
          AND (e.is_draft OR e.voided_at IS NOT NULL
               OR e.expense_type NOT IN ('BILL', 'EXPENSE', 'CASH_EXPENSE', 'VENDOR_CREDIT'))
          AND e.reception_date ~ {_SAFE} AND LEFT(e.reception_date, 10)::date BETWEEN %(start)s AND %(end)s""",
}


#: Open documents dated exactly on one day (the UTC-vs-Dubai boundary before 2026-10-07).
_BOUNDARY = {
    "receivables": f"""
        SELECT COALESCE(SUM(t.total - COALESCE(inc.amount_paid, 0)), 0) FROM income inc
        JOIN status_type st ON st.id = inc.status_type_id
        CROSS JOIN LATERAL (SELECT COALESCE(SUM(ii.line_amount + COALESCE(ii.tax_amount, 0)), 0) AS total
                            FROM income_items ii WHERE ii.income_id = inc.id) t
        WHERE inc.organization_id = %(org)s AND st.value IN ('PENDING', 'PARTIALLY_PAID') AND NOT inc.is_draft
          AND LEFT(inc.invoice_date, 10) = %(day)s""",
    "payables": f"""
        SELECT COALESCE(SUM(t.total - COALESCE(e.amount_paid, 0)), 0) FROM expense e
        JOIN status_type st ON st.id = e.status_type_id
        CROSS JOIN LATERAL (SELECT COALESCE(SUM(ei.line_amount + COALESCE(ei.tax_amount, 0)), 0) AS total
                            FROM expense_items ei WHERE ei.expense_id = e.id) t
        WHERE e.organization_id = %(org)s AND st.value IN ('PENDING', 'PARTIALLY_PAID') AND NOT e.is_draft
          AND LEFT(e.reception_date, 10) = %(day)s""",
}


def _dec(v: Any) -> Optional[Decimal]:
    return None if v is None else Decimal(str(v))


def classify(rec: Dict[str, Any], cur: Any) -> str:
    if "error" in rec:
        return "cube_error"
    sql, cube = _dec(rec.get("sql_value")), _dec(rec.get("cube_value"))
    if sql is not None and cube is not None and abs(cube - sql) <= TOL:
        return "match"
    if (sql is None or sql == 0) and (cube is None or cube == 0):
        return "same_empty"
    if rec["metric"] in BASIS_CHANGE:
        return "basis_change"
    if rec["metric"] in ("output_vat", "input_vat", "vat_payable") and sql is not None and cube is not None:
        period = {"org": rec["org_id"], "start": rec["period"]["start"], "end": rec["period"]["end"]}
        excluded = {}
        for side, query in _EXCLUDED_TAX.items():
            cur.execute(query, period)
            excluded[side] = Decimal(str(cur.fetchone()[0]))
        gap = sql - cube
        expected = {"output_vat": excluded["output"], "input_vat": excluded["input"],
                    "vat_payable": excluded["output"] - excluded["input"]}[rec["metric"]]
        if abs(gap - expected) <= TOL:
            return "rule_drafts"
    base = rec["metric"].replace("overdue_", "")
    if base in _BOUNDARY and sql is not None and cube is not None:
        cur.execute(_BOUNDARY[base], {"org": rec["org_id"], "day": rec["period"]["as_of"]})
        if abs((sql - cube) - Decimal(str(cur.fetchone()[0]))) <= TOL:
            return "fixed_day_boundary"
    return "unexplained"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default=str(_BACKEND / "logs" / "metrics_shadow.jsonl"))
    args = parser.parse_args()
    path = Path(args.log)
    records = []
    for candidate in (path.with_suffix(path.suffix + ".1"), path):
        if candidate.exists():
            records += [json.loads(line) for line in candidate.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        print(f"No shadow records in {path}")
        return 0

    conn = get_connection()
    conn.set_session(readonly=True)
    try:
        cur = conn.cursor()
        tagged = [(classify(r, cur), r) for r in records]
    finally:
        conn.close()
        close_pools()

    counts = collections.Counter(cat for cat, _ in tagged)
    by_metric = collections.Counter((r.get("metric", "error"), cat) for cat, r in tagged)
    unexplained = [r for cat, r in tagged if cat == "unexplained"]
    first = min(r.get("ts", "") for r in records)
    last = max(r.get("ts", "") for r in records)

    lines = [f"# Shadow review — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
             f"{len(records)} records from {first} to {last}.", "",
             "| Category | Records |", "|---|---|"]
    lines += [f"| {cat} | {n} |" for cat, n in counts.most_common()]
    lines += ["", "| Metric | Category | Records |", "|---|---|---|"]
    lines += [f"| {m} | {cat} | {n} |" for (m, cat), n in sorted(by_metric.items())]
    if unexplained:
        lines += ["", "## Unexplained", "", "| Metric | Org | SQL | Cube | Period |", "|---|---|---|---|---|"]
        lines += [f"| {r['metric']} | {r['org_id']} | {r.get('sql_value')} | {r.get('cube_value')} "
                  f"| {r['period']['start']}..{r['period']['end']} |" for r in unexplained[:100]]
    report = "\n".join(lines) + "\n"
    out = _BACKEND / "logs" / f"shadow_review_{dt.datetime.now():%Y%m%d_%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Written to {out}")
    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
