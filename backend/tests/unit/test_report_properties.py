"""Property tests: invariants that must hold for any data the builder accepts.

Seeded random generation (no extra dependency) over many shapes and sizes.
Each property is checked on a few hundred generated reports.
"""
import random
from decimal import Decimal

import pytest

from gemini_brain.artifacts.facts import build_factsheet
from gemini_brain.artifacts.insights import add_takeaways
from gemini_brain.artifacts.integrity import ground_text, reconcile
from gemini_brain.artifacts.ir import ReportDocument
from gemini_brain.artifacts.report_spec import build_report_spec

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
SEEDS = range(60)


def _doc(data, query, hint=None):
    return add_takeaways(ReportDocument.from_spec(build_report_spec(data, query, chart_hint=hint), query=query))


def _ranking(rng):
    n = rng.randint(1, 25)
    return [{"customer_name": f"Customer {i:02d} {rng.choice(['LLC', 'Co', 'Trading', 'Group'])}",
             "sales": round(rng.uniform(100, 2_000_000), 2)} for i in range(n)]


def _split(rng, total, parts):
    """`total` split into `parts` positive integers that add up exactly."""
    cuts = sorted(rng.sample(range(1, total), parts - 1))
    return [b - a for a, b in zip([0] + cuts, cuts + [total])]


def _pnl(rng):
    """A consistent statement: the months add up to the totals the line items give."""
    n = rng.randint(2, 12)
    items = [{"name": f"Cost line {i}", "amount": rng.randint(1_000, 90_000)} for i in range(rng.randint(2, 10))]
    expenses = sum(i["amount"] for i in items)
    revenue = rng.randint(expenses // 2, expenses * 3)
    monthly = [{"month": MONTHS[i], "revenue": r, "expenses": e}
               for i, (r, e) in enumerate(zip(_split(rng, revenue, n), _split(rng, expenses, n)))]
    return {"statement": "profit_and_loss", "monthly": monthly,
            "revenue": {"total": revenue, "line_items": [{"name": "Sales", "amount": revenue}]},
            "expenses": {"total": expenses, "line_items": items},
            "net_profit": revenue - expenses}


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("hint", [None, "bar", "pie", "donut"])
def test_rankings_reconcile_and_slices_add_up(seed, hint):
    rng = random.Random(seed)
    rows = _ranking(rng)
    doc = _doc(rows, "sales by customer", hint)
    assert [c.message for c in reconcile(doc) if not c.passed] == []
    total = sum(Decimal(str(r["sales"])) for r in rows)
    for chart in doc.charts:
        values = [v for v in chart.series[0].values if v is not None]
        if chart.chart_type in ("pie", "donut"):
            assert len(chart.categories) <= 8
            assert abs(sum(values) - total) < Decimal("0.01")  # slices + Other = the whole
            assert all(v > 0 for v in values)
        colors = chart.point_colors or []
        assert len(set(colors)) == len(colors)  # no two slices share a color


@pytest.mark.parametrize("seed", SEEDS)
def test_statements_reconcile_and_the_bridge_closes(seed):
    rng = random.Random(1000 + seed)
    doc = _doc(_pnl(rng), "P&L report")
    assert [c.message for c in reconcile(doc) if not c.passed] == []
    bridge = next(c for c in doc.charts if c.chart_type == "waterfall")
    vals = bridge.series[0].values
    assert abs(sum(vals[1:-1]) + vals[0] - vals[-1]) < Decimal("0.01")


@pytest.mark.parametrize("seed", SEEDS)
def test_every_takeaway_grounds_and_every_kpi_is_exact(seed):
    rng = random.Random(2000 + seed)
    for data, query in ((_ranking(rng), "sales by customer"), (_pnl(rng), "P&L report")):
        doc = _doc(data, query)
        facts = build_factsheet(doc)
        for chart in doc.charts:
            if chart.takeaway:
                assert ground_text(chart.takeaway, facts, scoped=False).removed == [], chart.takeaway
        for kpi in doc.kpis:
            if kpi.value is not None and kpi.unit == "money":
                assert kpi.display == f"AED {kpi.value.quantize(Decimal('0.01')):,.2f}"


@pytest.mark.parametrize("seed", SEEDS)
def test_missing_values_stay_missing(seed):
    rng = random.Random(3000 + seed)
    months = [{"month": MONTHS[i], "revenue": None if rng.random() < 0.3 else rng.randint(1_000, 90_000),
               "expenses": rng.randint(1_000, 90_000)} for i in range(rng.randint(3, 12))]
    doc = _doc({"statement": "profit_and_loss", "monthly": months}, "P&L")
    revenue = next(s for s in doc.charts[0].series if s.name == "Revenue")
    for month, v in zip(months, revenue.values):
        assert (v is None) == (month["revenue"] is None)
