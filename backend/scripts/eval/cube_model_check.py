"""
cube_model_check.py — Run the Cube model's SQL against Postgres without Cube.

For each cube in Gemini_Brain_Accutax/semantic/model/cubes this:
  1. runs the cube SQL for the given organizations (proves columns, joins and
     regexes are valid, and times it);
  2. computes every sum / count / count_distinct measure per organization the
     way Cube would (measure SQL plus measure filters), for flow cubes over the
     given period.

Read-only: one read-only transaction, 20 s statement timeout. Use the test
orgs; do not run it across all tenants on the shared database.

    python scripts/eval/cube_model_check.py --orgs 24-33 --start 2026-01-01 --end 2026-10-06
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from gemini_brain.db.connection import get_connection  # noqa: E402

SEMANTIC = Path(__file__).resolve().parents[3] / "semantic"
#: Cube -> its period column; cubes not listed are balances or current status.
FLOW_DATE = {"sales_lines": "document_date", "purchase_lines": "document_date", "vat_lines": "document_date"}
BALANCE_CUBES = {"gl_lines"}  # flow for P&L measures, balance for the rest; checked over the period here


def load_cubes() -> Dict[str, Dict[str, Any]]:
    cubes = {}
    for path in sorted((SEMANTIC / "model" / "cubes").glob("*.yml")):
        for cube in yaml.safe_load(path.read_text(encoding="utf-8"))["cubes"]:
            cubes[cube["name"]] = cube
    return cubes


def cube_sql(name: str, cubes: Dict[str, Dict[str, Any]]) -> str:
    cube = cubes[name]
    sql = cube.get("sql") or f"SELECT * FROM {cube['sql_table']}"
    return re.sub(r"\{([a-z_]+)\.sql\(\)\}", lambda m: cube_sql(m.group(1), cubes), sql)


def measure_expr(measure: Dict[str, Any]) -> str:
    sql = str(measure.get("sql") or "1").replace("{CUBE}", "t")
    conditions = [f"({str(f['sql']).replace('{CUBE}', 't')})" for f in measure.get("filters") or []]
    value = f"CASE WHEN {' AND '.join(conditions)} THEN {sql} END" if conditions else sql
    kind = measure["type"]
    if kind == "sum":
        return f"SUM({value})"
    if kind == "count":
        return f"COUNT(CASE WHEN {' AND '.join(conditions)} THEN 1 END)" if conditions else "COUNT(*)"
    if kind == "count_distinct":
        return f"COUNT(DISTINCT {value})"
    raise ValueError(kind)


def literal(sql: str) -> str:
    """Model SQL for psycopg2: a literal % (as in ILIKE '%cash%') must be doubled next to %s parameters."""
    return sql.replace("%", "%%")


def run(cur, sql: str, params: Tuple) -> Tuple[List[str], List[tuple], float]:
    t0 = time.time()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [d[0] for d in cur.description], rows, time.time() - t0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgs", default="24-33", help="range like 24-33 or a list like 24,25")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-10-06")
    args = parser.parse_args()
    if "-" in args.orgs:
        lo, hi = (int(x) for x in args.orgs.split("-"))
        orgs = list(range(lo, hi + 1))
    else:
        orgs = [int(x) for x in args.orgs.split(",")]

    cubes = load_cubes()
    conn = get_connection()
    conn.set_session(readonly=True, autocommit=False)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '20s'")
    failures = 0
    for name, cube in cubes.items():
        sql = literal(cube_sql(name, cubes))
        org_col = "id" if name == "organizations" else "organization_id"
        print(f"\n=== {name}")
        try:
            _, rows, secs = run(cur, f"SELECT COUNT(*) FROM ({sql}) t WHERE t.{org_col} = ANY(%s)", (orgs,))
            print(f"rows for orgs: {rows[0][0]}  ({secs:.2f}s)")
            measures = [m for m in cube.get("measures", []) if m["type"] in ("sum", "count", "count_distinct")]
            if not measures:
                continue
            where = f"t.{org_col} = ANY(%s)"
            params: Tuple = (orgs,)
            if name in FLOW_DATE or name in BALANCE_CUBES:
                date_col = FLOW_DATE.get(name, "transaction_date")
                where += f" AND t.{date_col} BETWEEN %s AND %s"
                params = (orgs, args.start, args.end)
            select = ", ".join(f"ROUND(({literal(measure_expr(m))})::numeric, 2) AS {m['name']}" for m in measures)
            cols, rows, secs = run(
                cur, f"SELECT t.{org_col} AS org, {select} FROM ({sql}) t WHERE {where} GROUP BY 1 ORDER BY 1", params)
            print(" | ".join(cols) + f"   ({secs:.2f}s)")
            for r in rows:
                print(" | ".join("" if v is None else str(v) for v in r))
        except Exception as e:  # report every cube, then fail
            failures += 1
            conn.rollback()
            cur.execute("SET statement_timeout = '20s'")
            print(f"FAILED: {type(e).__name__}: {str(e).splitlines()[0][:300]}")
    conn.rollback()
    conn.close()
    print(f"\n{len(cubes) - failures}/{len(cubes)} cubes OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
