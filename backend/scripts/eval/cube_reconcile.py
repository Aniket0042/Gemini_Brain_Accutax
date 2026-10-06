"""
cube_reconcile.py — Check Cube's P&L and balance sheet against Accutax and the ledger.

Guide section 11.3. For each organization and window:
  - pnl: Cube vs Accutax GET /report/profit-loss-with-accounts (revenue,
    cost of sales, operating expenses, net profit). That endpoint filters on
    organization_id, so it is safe to call per org.
  - balance_sheet: balance_difference must be 0 (reports seed defects, does not fix them).

Writes a CSV of every difference above 0.01 to logs/ and exits non-zero when
any exists. Run where both Cube and the Accutax API are reachable (the VM).

    python scripts/eval/cube_reconcile.py --orgs 24-33
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gemini_brain.api_client.accutax_client import call_api  # noqa: E402
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
    ok, body = call_api("/report/profit-loss-with-accounts", {},
                        {"organization_id": org, "start_date": start, "end_date": end}, timeout=60)
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgs", default="24-33")
    args = parser.parse_args()
    lo, _, hi = args.orgs.partition("-")
    orgs = list(range(int(lo), int(hi or lo) + 1))
    diffs: List[List[str]] = []

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
