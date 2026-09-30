"""
multi_org_eval.py — End-to-end evaluation of multi-organization answers.

Runs every case in tests/data/multi_org_eval_cases.json through the real
multi-org pipeline (planner, per-org fetch, merge, one summary model call)
against the configured database and Bedrock, then checks each answer for:

  layout      the expected layout (metric / per_org / merged / overlap / collapsed / direct)
  metric      for metric cases, the expected metric
  figures     for metric cases, every org's value against an independent SQL query
  order       ranking direction when the case names one
  length      answer word count within the layout's limit
  llm_calls   model calls within budget (3 + one per org whose planned fetch fell back)
  status      not "failed"

Costs real Bedrock calls: about one to three per case.

Usage (from backend/):
  .venv/Scripts/python scripts/eval/multi_org_eval.py --orgs 27,29
  .venv/Scripts/python scripts/eval/multi_org_eval.py --orgs 24,25,26,27,28,29,30,31,32,33 --only s1_,s4_
Writes a JSON report next to this script (multi_org_eval_<timestamp>.json) and
exits non-zero when any case fails.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for path in (_BACKEND_ROOT / "src", _BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner  # noqa: E402
from gemini_brain.orchestrator.multi_org import run_multi_org  # noqa: E402
from gemini_brain.orchestrator.multi_org_metrics import BY_KEY, metric_selection  # noqa: E402
from gemini_brain.orchestrator.multi_org_plan import plan_query  # noqa: E402
from gemini_brain.sql_fallback.db_connection import get_connection  # noqa: E402

CASES_FILE = _BACKEND_ROOT / "tests" / "data" / "multi_org_eval_cases.json"
BASE_LLM_BUDGET = 3  # classify + select + summary; a metric or fast-router plan needs 1

# ── Independent figures ──────────────────────────────────────────────────────
# Written separately from reports/definitions.py on purpose: a check that
# reused the report code would pass even when that code is wrong.

_PERIOD_SQL = {
    "revenue": """
        SELECT COALESCE(SUM(ii.line_amount), 0)
        FROM income inc JOIN income_items ii ON ii.income_id = inc.id
        JOIN status_type st ON st.id = inc.status_type_id
        WHERE inc.organization_id = %s AND st.value NOT IN ('CANCELLED','VOIDED')
          AND inc.invoice_date::date BETWEEN %s AND %s""",
    "expenses": """
        SELECT COALESCE(SUM(ei.line_amount), 0)
        FROM expense e JOIN expense_items ei ON ei.expense_id = e.id
        JOIN status_type st ON st.id = e.status_type_id
        WHERE e.organization_id = %s AND st.value NOT IN ('CANCELLED','VOIDED')
          AND e.reception_date::date BETWEEN %s AND %s""",
}

_OPEN_SQL = """
    SELECT COALESCE(SUM(total - paid), 0),
           COALESCE(SUM(total - paid) FILTER (WHERE due < %s::date), 0)
    FROM (
        SELECT d.id, d.due_date::date AS due, COALESCE(d.amount_paid, 0) AS paid,
               SUM(it.line_amount::numeric + COALESCE(it.tax_amount::numeric, 0)) AS total
        FROM {table} d JOIN {items} it ON it.{fk} = d.id
        JOIN status_type st ON st.id = d.status_type_id
        WHERE d.organization_id = %s AND st.value IN ('PENDING','PARTIALLY_PAID')
          AND d.{date_col}::date <= %s::date
        GROUP BY d.id, d.due_date, d.amount_paid
    ) t"""

_OPEN_TABLES = {
    "receivables": ("income", "income_items", "income_id", "invoice_date"),
    "payables": ("expense", "expense_items", "expense_id", "reception_date"),
}


def expected_value(metric_key: str, org_id: int, question: str, cur: Any) -> Optional[float]:
    """The metric's figure for one org from independent SQL; None if not checked."""
    qp = metric_selection(BY_KEY[metric_key], question)["query_params"]
    if metric_key in _PERIOD_SQL:
        cur.execute(_PERIOD_SQL[metric_key], (org_id, qp["start_date"], qp["end_date"]))
        return float(cur.fetchone()[0])
    if metric_key == "net_profit":
        income = expected_value("revenue", org_id, question, cur)
        expenses = expected_value("expenses", org_id, question, cur)
        return round(income - expenses, 2)
    base = metric_key.replace("overdue_", "")
    if base in _OPEN_TABLES:
        table, items, fk, date_col = _OPEN_TABLES[base]
        as_of = qp["as_of_date"]
        cur.execute(_OPEN_SQL.format(table=table, items=items, fk=fk, date_col=date_col), (as_of, org_id, as_of))
        outstanding, overdue = cur.fetchone()
        return float(overdue if metric_key.startswith("overdue_") else outstanding)
    return None  # cash_balance: not independently checked


# ── Evaluation ───────────────────────────────────────────────────────────────

def layout_of(result: Dict[str, Any]) -> str:
    routing = result.get("routing_info") or {}
    if routing.get("path") == "multi_org_direct":
        return "direct"
    return routing.get("layout") or "unknown"


def evaluate_case(case: Dict[str, Any], orgs: List[int], meta: Dict[int, Dict[str, Any]],
                  word_limits: Dict[str, int], cur: Any) -> Dict[str, Any]:
    t0 = time.time()
    result = run_multi_org(
        case["question"], orgs, meta,
        runner_factory=GeminiBrainRunner,
        run_kwargs={"allowed_org_ids": orgs, "user_id": 0, "db_name": "", "use_api": True,
                    "auth_token": "", "model": "auto", "effort": None, "ui_context": None, "brief": False},
        planner=plan_query,
    )
    elapsed = round(time.time() - t0, 1)
    usage = result.get("token_usage") or {}
    fallbacks = sum(1 for step in result.get("agent_trace") or []
                    if step.get("step") == "org_result" and step.get("path") != "multi_org_plan")
    words = len((result.get("answer") or "").split())
    checks: Dict[str, Any] = {}

    got_layout = layout_of(result)
    checks["layout"] = {"ok": got_layout == case["layout"], "got": got_layout, "want": case["layout"]}
    limit = word_limits.get(case["layout"], 220)
    checks["length"] = {"ok": words <= limit, "got": words, "want": f"<= {limit}"}
    budget = BASE_LLM_BUDGET + fallbacks * 3
    checks["llm_calls"] = {"ok": int(usage.get("llm_calls") or 0) <= budget,
                           "got": usage.get("llm_calls"), "want": f"<= {budget}"}
    checks["status"] = {"ok": result.get("status") != "failed", "got": result.get("status")}

    comparison = result.get("comparison")
    if case["layout"] == "metric":
        got_metric = (comparison or {}).get("metric")
        checks["metric"] = {"ok": got_metric == case["metric"], "got": got_metric, "want": case["metric"]}
        if comparison and got_metric == case["metric"]:
            mismatches = []
            for row in comparison["rows"]:
                want = expected_value(case["metric"], row["organization_id"], case["question"], cur)
                if want is not None and abs(want - row["value"]) > 0.01:
                    mismatches.append({"organization": row["organization"], "got": row["value"], "want": want})
            checks["figures"] = {"ok": not mismatches, "mismatches": mismatches,
                                 "checked": case["metric"] != "cash_balance"}
            if case.get("order"):
                checks["order"] = {"ok": comparison.get("order") == case["order"],
                                   "got": comparison.get("order"), "want": case["order"]}

    return {
        "id": case["id"],
        "question": case["question"],
        "passed": all(c["ok"] for c in checks.values()),
        "checks": checks,
        "elapsed_seconds": elapsed,
        "llm_calls": usage.get("llm_calls"),
        "cost_usd": usage.get("cost_usd"),
        "answer_words": words,
        "answer": result.get("answer"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--orgs", required=True, help="Comma-separated organization IDs, e.g. 27,29")
    parser.add_argument("--only", default="", help="Comma-separated case-id prefixes to run")
    args = parser.parse_args()

    orgs = [int(o) for o in args.orgs.split(",") if o.strip()]
    spec = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    prefixes = [p for p in args.only.split(",") if p]
    cases = [c for c in spec["cases"] if not prefixes or any(c["id"].startswith(p) for p in prefixes)]

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, currency FROM organizations WHERE id = ANY(%s)", (orgs,))
    meta = {int(i): {"name": n, "currency": c or ""} for i, n, c in cur.fetchall()}

    report: List[Dict[str, Any]] = []
    for case in cases:
        try:
            outcome = evaluate_case(case, orgs, meta, spec["word_limits"], cur)
        except Exception as e:  # one broken case must not hide the others
            outcome = {"id": case["id"], "question": case["question"], "passed": False,
                       "checks": {"error": {"ok": False, "got": f"{type(e).__name__}: {e}"}}}
        report.append(outcome)
        failed = [k for k, v in outcome["checks"].items() if not v["ok"]]
        mark = "PASS" if outcome["passed"] else "FAIL " + ",".join(failed)
        print(f"{mark:32} {case['id']:26} {outcome.get('elapsed_seconds', '-'):>6}s "
              f"llm={outcome.get('llm_calls', '-')} words={outcome.get('answer_words', '-')}", flush=True)
    conn.close()

    passed = sum(1 for r in report if r["passed"])
    total_cost = round(sum(r.get("cost_usd") or 0 for r in report), 4)
    print(f"\n{passed}/{len(report)} passed · {len(orgs)} orgs · est. cost ${total_cost}")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(__file__).with_name(f"multi_org_eval_{stamp}.json")
    out.write_text(json.dumps({"orgs": orgs, "passed": passed, "total": len(report), "cases": report},
                              indent=2, default=str), encoding="utf-8")
    print(f"Report: {out}")
    return 0 if passed == len(report) else 1


if __name__ == "__main__":
    sys.exit(main())
