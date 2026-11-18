"""By-month trends over several organizations come with a code-built table (periods as rows, Org columns, totals)."""
from decimal import Decimal
from types import SimpleNamespace

from gemini_brain.semantic import query_metrics as qm

VIEW = SimpleNamespace(name="pnl", time_dimension="pnl.transaction_date")


def _rows(spec):
    return [{"pnl.organization_id": org, "pnl.organization_name": f"Name {org}", "pnl.currency": "AED",
             "pnl.transaction_date.month": f"{month}-01T00:00:00.000", "pnl.revenue": Decimal(rev)}
            for (org, month, rev) in spec]


def _summarize(rows):
    query = {"measures": ["pnl.revenue"], "limit": 300}
    return qm.summarize(rows, query, VIEW, "month")


def test_pivot_has_periods_as_rows_and_org_columns():
    out = _summarize(_rows([("24", "2026-01", "100"), ("24", "2026-02", "-5"), ("27", "2026-01", "7")]))
    p = out["pivot"]
    assert p["columns"] == {"Org 24": "Name 24", "Org 27": "Name 27"}
    rows = p["measures"]["pnl.revenue"]["rows"]
    assert rows[0] == {"period": "2026-01", "Org 24": "100", "Org 27": "7"}
    assert rows[1] == {"period": "2026-02", "Org 24": "-5", "Org 27": "no data"}


def test_pivot_total_is_worked_out_in_code():
    out = _summarize(_rows([("24", "2026-01", "100"), ("24", "2026-02", "-5"), ("27", "2026-01", "7")]))
    assert out["pivot"]["measures"]["pnl.revenue"]["total"] == {"period": "Total", "Org 24": "95", "Org 27": "7"}


def test_no_pivot_for_one_organization():
    assert "pivot" not in _summarize(_rows([("24", "2026-01", "1"), ("24", "2026-02", "2")]))


def test_no_pivot_when_too_many_periods():
    spec = [(org, f"2025-{m:02d}", "1") for org in ("24", "27") for m in range(1, 13)] + [("24", "2026-01", "1")]
    assert "pivot" not in _summarize(_rows(spec))


def _one_org(spec):
    return [{"pnl.organization_id": "33", "pnl.organization_name": "Name 33", "pnl.currency": "AED",
             "pnl.transaction_date.month": f"{month}-01T00:00:00.000", "pnl.revenue": Decimal(rev), "pnl.net_profit": Decimal(np)}
            for (month, rev, np) in spec]


def test_one_organization_gets_a_period_by_measure_table():
    rows = _one_org([("2026-01", "10", "4"), ("2026-02", "-3", "-1")])
    query = {"measures": ["pnl.revenue", "pnl.net_profit"], "limit": 300}
    p = qm.summarize(rows, query, VIEW, "month")["pivot"]
    assert p["organization"] == "Name 33"
    assert p["rows"][0] == {"period": "2026-01", "revenue": "10", "net_profit": "4"}
    assert p["total"] == {"period": "Total", "revenue": "7", "net_profit": "3"}


def test_one_organization_one_measure_has_no_table():
    rows = _one_org([("2026-01", "10", "4")])
    assert "pivot" not in qm.summarize(rows, {"measures": ["pnl.revenue"], "limit": 300}, VIEW, "month")
