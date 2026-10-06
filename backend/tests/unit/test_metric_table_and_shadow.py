"""metric_table (one Cube query per view) and the shadow comparison log."""
import json
import time
from decimal import Decimal
from types import SimpleNamespace

import pytest

from gemini_brain.semantic import cube_client, metric_table, shadow


@pytest.fixture(autouse=True)
def _no_presidio(monkeypatch):
    """The real redactor loads spaCy on first use, which can outlast the thread joins below."""
    import gemini_brain.pii.redactor as redactor

    monkeypatch.setattr(redactor, "redact_pii", lambda text: (text, []))


def _result(rows):
    return cube_client.CubeResult(rows=rows, query={}, elapsed_ms=1)


def test_one_query_per_view_for_all_orgs(monkeypatch):
    queries = []

    def fake_load(query, *, organization_ids, subject, deadline):
        queries.append(query)
        view = query["measures"][0].split(".")[0]
        if view == "pnl":
            return _result([{"pnl.organization_id": Decimal(24), "pnl.net_profit": Decimal("5.5"),
                             "pnl.net_margin_pct": None}])
        return _result([
            {"balance_sheet.organization_id": Decimal(24), "balance_sheet.account_currency": "AED",
             "balance_sheet.cash_and_bank": Decimal("10")},
            {"balance_sheet.organization_id": Decimal(25), "balance_sheet.account_currency": "AED",
             "balance_sheet.cash_and_bank": Decimal("1")},
            {"balance_sheet.organization_id": Decimal(25), "balance_sheet.account_currency": "USD",
             "balance_sheet.cash_and_bank": Decimal("2")},
        ])

    monkeypatch.setattr(metric_table.cube_client, "load", fake_load)
    table, mixed = metric_table.metric_table(
        ["net_profit", "profit_margin", "cash_balance", "revenue_growth"],
        organization_ids=[24, 25, 26], start="2026-01-01", end="2026-10-06", as_of="2026-10-06",
        subject="t", deadline=time.monotonic() + 5)
    assert len(queries) == 2
    assert queries[0]["timeDimensions"] == [{"dimension": "pnl.transaction_date",
                                             "dateRange": ["2026-01-01", "2026-10-06"]}]
    assert queries[1]["timeDimensions"][0]["dateRange"] == ["1900-01-01", "2026-10-06"]
    assert table[24] == {"net_profit": Decimal("5.5"), "profit_margin": None, "cash_balance": Decimal("10")}
    assert table[25] == {"cash_balance": None}  # two currencies: never added up
    assert table[26] == {}                     # nothing recorded, not zero
    assert mixed == [25]


def test_compare_record():
    rec = shadow.compare_record("net_profit", "pnl.net_profit", 24, 100.0, "", Decimal("100.004"), False, True)
    assert rec["match"] is True and rec["basis_change"] is True
    rec = shadow.compare_record("cash_balance", "balance_sheet.cash_and_bank", 25, 5.0, "", None, True, True)
    assert rec["match"] is None and rec["cube_reason"] == "mixed currencies" and rec["basis_change"] is False
    rec = shadow.compare_record("receivables", "receivables.outstanding", 26, None, "nothing recorded", None, False, False)
    assert rec["cube_reason"] == "nothing recorded" and rec["sql_reason"] == "nothing recorded"


def _plan():
    selection = {"key": "rpt_profit_summary", "query_params": {"start_date": "2026-01-01", "end_date": "2026-10-06"}}
    return SimpleNamespace(series=None, metrics=["net_profit", "revenue_growth"], metric=None,
                           selection=selection, all_selections=lambda: [selection])


def _run(org, net_profit):
    payload = {"summary": {"net_profit": net_profit, "total_income": 1, "total_expenses": 1}}
    return SimpleNamespace(org_id=org, answered=True,
                           result={"status": "ok", "payloads": {"rpt_profit_summary": payload}})


def test_shadow_writes_one_line_per_org_and_metric(monkeypatch, tmp_path):
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(shadow.settings, "metrics_shadow_log", str(log))
    calls = []

    def fake_table(keys, **kwargs):
        calls.append((keys, kwargs["start"], kwargs["end"], kwargs["organization_ids"]))
        return {24: {"net_profit": Decimal("100")}, 25: {}}, []

    monkeypatch.setattr(metric_table, "metric_table", fake_table)
    thread = shadow.start_shadow_compare(_plan(), [_run(24, 100.0), _run(25, 7.0)], "net profit by org", user_id=9)
    thread.join(timeout=10)
    assert calls == [(["net_profit"], "2026-01-01", "2026-10-06", [24, 25])]
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(r["org_id"], r["match"]) for r in lines] == [(24, True), (25, None)]
    assert lines[1]["cube_reason"] == "nothing recorded"
    assert "user" not in json.dumps(lines)


def test_shadow_records_a_cube_failure_and_never_raises(monkeypatch, tmp_path):
    log = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(shadow.settings, "metrics_shadow_log", str(log))

    def failing(keys, **kwargs):
        raise cube_client.CubeError("Cube unreachable")

    monkeypatch.setattr(metric_table, "metric_table", failing)
    shadow.start_shadow_compare(_plan(), [_run(24, 1.0)], "q", user_id=1).join(timeout=10)
    record = json.loads(log.read_text(encoding="utf-8"))
    assert "Cube unreachable" in record["error"]


@pytest.mark.parametrize("plan", [None, SimpleNamespace(series={"metric": "revenue"}, metrics=[], metric=None)])
def test_shadow_skips_plans_without_mapped_metrics(plan):
    assert shadow.start_shadow_compare(plan, [], "q", user_id=1) is None
