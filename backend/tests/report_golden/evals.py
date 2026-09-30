"""Evaluation sets for report quality, with scoring.

* Chart intent — does the report show the form the user asked for, or, when
  the data cannot take that form, fall back with a stated reason?
* Narrative grounding — of statements about a fixture's data, are the wrong
  ones removed and the true ones kept?

Used by tests/unit/test_report_evals.py (gates) and scripts/eval/report_eval.py
(the printed scorecard, optionally with live model narration).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from tests.report_golden.fixtures import AGING, CUSTOMERS, FIXTURES, MANY_CUSTOMERS, MONTHLY, PNL, VAT_MONTHLY

MONTHLY_REVENUE = [{"month": m["month"], "revenue": m["revenue"]} for m in MONTHLY]
NEGATIVES = [{"account_name": "Sales", "net_amount": 50000}, {"account_name": "Refunds", "net_amount": -4200},
             {"account_name": "Other Income", "net_amount": 3100}]
PNL_MONTHS = {"statement": "profit_and_loss", "monthly": MONTHLY}

#: (request, data, expected chart form of the first chart, text the notes must contain or None)
CHART_INTENT: List[Dict[str, Any]] = [
    # the named form is honoured
    {"query": "pie chart of sales by customer", "data": CUSTOMERS, "expect": "pie"},
    {"query": "show sales by customer as a pie", "data": CUSTOMERS[:5], "expect": "pie"},
    {"query": "donut chart of sales by customer", "data": CUSTOMERS, "expect": "donut"},
    {"query": "doughnut of sales by customer as pdf", "data": MANY_CUSTOMERS, "expect": "donut",
     "note": "grouped into Other"},
    {"query": "bar chart of sales by customer", "data": CUSTOMERS, "expect": "bar"},
    {"query": "column chart of sales by customer", "data": CUSTOMERS[:4], "expect": "bar"},
    {"query": "horizontal bar of sales by customer", "data": CUSTOMERS[:3], "expect": "hbar"},
    {"query": "line chart of revenue by month", "data": MONTHLY_REVENUE, "expect": "line"},
    {"query": "revenue trend", "data": MONTHLY_REVENUE, "expect": "line"},
    {"query": "area chart of revenue by month", "data": MONTHLY_REVENUE, "expect": "area"},
    {"query": "stacked bar of vat and net sales by month", "data": VAT_MONTHLY, "expect": "stacked_bar"},
    {"query": "show the P&L waterfall", "data": PNL, "expect": "waterfall"},
    {"query": "profit bridge for Q2 as pdf", "data": PNL, "expect": "waterfall"},
    {"query": "pie chart of income vs expense", "data": {"graphData": {
        "labels": ["Jan 2026", "Feb 2026"], "incomeValues": [100, 300], "expenseValues": [50, 50],
        "cashflowValues": [50, 250]}}, "expect": "pie", "note": "Cash Flow"},
    {"query": "pie chart of tax by customer", "data": [
        {"customer_name": "A", "sales": 100, "tax": 5}, {"customer_name": "B", "sales": 80, "tax": 4}],
     "expect": "pie"},
    # the named form cannot honestly show this data: fall back, say why
    {"query": "pie of net amount by account", "data": NEGATIVES, "expect": "bar", "note": "negative"},
    {"query": "donut of net amount by account", "data": NEGATIVES, "expect": "bar", "note": "negative"},
    {"query": "stacked bar of revenue and expenses by month", "data": PNL_MONTHS, "expect": "bar",
     "note": "side by side"},
    {"query": "waterfall chart of sales by customer", "data": CUSTOMERS[:4], "expect": "bar",
     "note": "starting total"},
    {"query": "line chart of revenue", "data": [{"month": "April", "revenue": 52400}], "expect": "bar",
     "note": "only one data point"},
    # no named form: the data decides
    {"query": "sales by customer chart", "data": CUSTOMERS, "expect": "hbar"},
    {"query": "chart of amount by name", "data": [{"name": "A", "amount": 3}, {"name": "B", "amount": 2}], "expect": "bar"},
    {"query": "breakdown chart of sales by customer", "data": CUSTOMERS[:6], "expect": "donut"},
    {"query": "share of sales by customer chart", "data": CUSTOMERS[:5], "expect": "donut"},
    {"query": "sales mix by customer chart", "data": NEGATIVES, "expect": "bar"},
    {"query": "P&L report as pdf", "data": PNL, "expect": "bar"},
    {"query": "revenue by month chart", "data": MONTHLY_REVENUE, "expect": "line"},
    {"query": "outstanding receivables by customer chart", "data": AGING, "expect": "hbar"},
    {"query": "chart of accounts", "data": CUSTOMERS, "expect": None},  # an accounting report, not a chart
    {"query": "Bridge Corp sales chart", "data": CUSTOMERS[:3], "expect": "hbar"},
]

#: (fixture data, request, statement, should it be kept?)
NARRATIVE: List[Dict[str, Any]] = [
    # true statements — must survive
    *[{"data": CUSTOMERS, "query": "sales by customer", "text": t, "keep": True} for t in (
        "Apex Retail Trading LLC leads with AED 220,500.00 in sales.",
        "Apex Retail Trading LLC leads with AED 220.5K.",
        "Apex Retail Trading LLC brought in about AED 220,000.",
        "Apex Retail Trading LLC holds 25.1% of sales.",
        "The top three customers make up 67.4% of sales.",
        "Total sales were AED 880,175.",
        "Total sales were about AED 0.88M.",
        "Zenith Supplies is the smallest customer at AED 4,000.",
        "Falcon Energy follows with AED 191.3K.",
        "Apex Retail Trading LLC is ahead of Falcon Energy by AED 29,190.",
        "The leader sells about 1.15 times the runner-up.",
        "Sales are spread across 10 customers.",
        "In Q2 2026 the top 3 customers stood out.",
        "Over the last 90 days sales held up.",
    )],
    *[{"data": PNL, "query": "P&L report", "text": t, "keep": True} for t in (
        "Net profit was AED 59,640 on revenue of AED 174,300.",
        "The margin was 34.2%.",
        "Payroll was 24.4% of revenue.",
        "Revenue grew 21.6% from April to June.",
        "Expenses rose by AED 2,700 from April to May.",
        "Cost of Goods is the largest cost at AED 52.3K.",
        "Expenses grew 28.3% from April to June, faster than revenue.",
        "Total expenses were AED 114,660.",
        "Revenue exceeded expenses by AED 59.6K.",
        "Costs of AED 114.7K take revenue to net profit of AED 59.6K.",
    )],
    # true statements a live narrator wrote that stricter scoping once removed:
    # month names vs "Mar 2026" labels, plurals, a name without "LLC", no period named
    *[{"data": FIXTURES[name]["data"], "query": FIXTURES[name]["query"], "text": t, "keep": True} for name, t in (
        ("dashboard_graph", "While income declined 10% over the period, expenses grew steadily, rising 16.7% to reach AED 70,000 in March."),
        ("dashboard_graph", "February saw the strongest performance with income of AED 120,000 and the widest income-expense gap of AED 55,000."),
        ("dashboard_graph", "The opposing trends of falling income (-10%) and rising expenses (+16.7%) are squeezing margins."),
        ("aging_outstanding", "Apex Retail Trading dominates both total outstanding (66.7%) and current balances (70.6%)."),
        ("vat_trend", "VAT liability rises steadily to AED 3,185 in June, tracking 21.6% growth in sales."),
        ("vat_trend", "The gap between net sales and VAT was largest in June at AED 60.5K."),
        ("pnl_net_mismatch", "Strong Q2 profit of AED 70.0K at 34.2% margin, but monthly figures need review."),
    )],
    # wrong statements — must be removed
    *[{"data": CUSTOMERS, "query": "sales by customer", "text": t, "keep": False} for t in (
        "Apex Retail Trading LLC made AED 250,000 in sales.",
        "The top three customers make up 90% of sales.",
        "Total sales were AED 1.5M.",
        "Sales grew 40% year on year.",
        "Falcon Energy contributed 31.4% of sales.",
        "Apex Retail Trading LLC sells nearly six times the runner-up.",
        "Sales doubled.",
        "Average sales per customer were AED 120,000.",
        "Zenith Supplies is the smallest at AED 14,000.",
        "The leader is ahead by AED 45,000.",
    )],
    # figures a live narrator computed itself (not in its fact brief)
    *[{"data": FIXTURES["kpis_mixed_units"]["data"], "query": FIXTURES["kpis_mixed_units"]["query"], "text": t,
       "keep": False} for t in (
        "Income represents 64.1% of total flows, with expenses at 35.9%.",
        "Total financial flows reached AED 2.51M.",
    )],
    *[{"data": PNL, "query": "P&L report", "text": t, "keep": False} for t in (
        "Net profit was AED 65,000.",
        "The margin was 40%.",
        "Payroll was 30% of revenue.",
        "Revenue grew 35% from April to June.",
        "Revenue tripled.",
        "Marketing spend reached AED 9,000.",
        "Total expenses were AED 120,000.",
        "Costs took revenue down to AED 45.0K.",
    )],
]


@dataclass
class Scored:
    passed: int
    total: int
    failures: List[str]

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 1.0


def _canvas(query: str, data: Any) -> Optional[Dict[str, Any]]:
    from tests.report_golden.harness import _offline_attach

    return _offline_attach(query, data)


def run_chart_intent() -> Scored:
    failures = []
    for case in CHART_INTENT:
        spec = _canvas(case["query"], case["data"])
        got = spec["charts"][0]["chart_type"] if spec and spec.get("charts") else None
        ok = got == case["expect"]
        note = case.get("note")
        if ok and note:
            ok = any(note in n for n in (spec or {}).get("notes") or [])
        if not ok:
            failures.append(f"{case['query']!r}: expected {case['expect']}"
                            + (f" with note {note!r}" if note else "") + f", got {got}")
    return Scored(len(CHART_INTENT) - len(failures), len(CHART_INTENT), failures)


def run_narrative() -> Dict[str, Scored]:
    """Separate scores for true statements kept and wrong statements removed."""
    from gemini_brain.artifacts.facts import build_factsheet
    from gemini_brain.artifacts.insights import add_takeaways
    from gemini_brain.artifacts.integrity import ground_text
    from gemini_brain.artifacts.ir import ReportDocument
    from gemini_brain.artifacts.report_spec import build_report_spec

    cache: Dict[int, Any] = {}
    kept, removed = [], []
    for case in NARRATIVE:
        key = id(case["data"])
        if key not in cache:
            cache[key] = build_factsheet(add_takeaways(
                ReportDocument.from_spec(build_report_spec(case["data"], case["query"]), query=case["query"])))
        was_kept = not ground_text(case["text"], cache[key]).removed
        (kept if case["keep"] else removed).append((case["text"], was_kept == case["keep"]))
    return {
        "true_kept": Scored(sum(ok for _, ok in kept), len(kept), [t for t, ok in kept if not ok]),
        "wrong_removed": Scored(sum(ok for _, ok in removed), len(removed), [t for t, ok in removed if not ok]),
    }
