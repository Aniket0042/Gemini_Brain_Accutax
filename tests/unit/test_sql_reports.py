"""
test_sql_reports.py — Stage 5: deterministic SQL reports as first-class tools.

These reports cannot be executed here (no database in unit tests), so the tests
lean hard on what *can* be checked statically. Two of those checks earn their
keep specifically because the SQL is unverified:

  - placeholder/parameter arity, which is the single most likely defect in ported
    parameterised SQL and fails at call time rather than import time;
  - that org_id is always bound as a parameter and never formatted into the SQL,
    which is the difference between tenant isolation and a tenant leak.
"""
import re
from unittest.mock import MagicMock, patch

import pytest

from gemini_brain.reports import definitions as defs
from gemini_brain.reports.engine import parse_date, run_report_safe, serialize_rows, total_of
from gemini_brain.resilience.outcomes import Outcome
from gemini_brain.sql_fallback.sql_safety import assert_read_only
from gemini_brain.tools.context import RequestCtx
from gemini_brain.tools.registry import REGISTRY
from gemini_brain.tools.schemas import (
    ReportAsOfParams,
    ReportPeriodLimitParams,
    ReportPeriodParams,
)


ORG = 199
REPORT_KEYS = sorted(defs.REPORTS)


@pytest.fixture
def captured_sql(monkeypatch):
    """Intercept every query() the reports issue, returning canned rows."""
    calls = []

    def fake_query(sql, params, org_id, db_name=""):
        calls.append({"sql": sql, "params": params, "org_id": org_id, "db_name": db_name})
        return [
            {"customer": "Acme LLC", "outstanding": 1000.0, "total_amount": 1000.0,
             "revenue": 1000.0, "amount": 1000.0, "total_revenue": 1000.0,
             "total_purchases": 1000.0, "balance_due": 250.0, "output_vat": 50.0,
             "input_vat": 20.0, "standard_rated_sales": 1000.0,
             "standard_rated_purchases": 400.0, "count": 4, "status": "ACCEPTED"},
        ]

    monkeypatch.setattr(defs, "query", fake_query)
    return calls


# ── Registry wiring ──────────────────────────────────────────────────────────

def test_every_registered_report_has_a_definition():
    registered = {spec.endpoint for spec in REGISTRY.values() if spec.endpoint.startswith("rpt_")}
    assert registered == set(defs.REPORTS), (
        "registry and reports/definitions.py disagree — a tool the router can pick "
        "with no implementation behind it fails only at query time"
    )


def test_report_tools_declare_params_and_formatter():
    for name, spec in REGISTRY.items():
        if not spec.endpoint.startswith("rpt_"):
            continue
        assert spec.params is not None, name
        assert spec.formatter, name
        assert spec.description.strip(), name


def test_report_descriptions_disambiguate_from_existing_tools():
    """Near-identical descriptions make routing worse. The overlapping pairs must
    tell the router which one to pick."""
    assert "ar_aging instead" in REGISTRY["aged_receivables_detail"].description
    assert "vat_summary instead" in REGISTRY["vat_input_output"].description


# ── Static SQL checks ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", REPORT_KEYS)
def test_placeholder_count_matches_parameter_count(key, captured_sql):
    """The most likely defect in ported parameterised SQL, and it fails at runtime.

    psycopg2 raises only when the query is executed, so without this the mistake
    would reach production untested.
    """
    defs.REPORTS[key]({}, ORG, "")
    assert captured_sql, f"{key} issued no query"
    for call in captured_sql:
        placeholders = len(re.findall(r"%s", call["sql"]))
        supplied = len(call["params"])
        assert placeholders == supplied, (
            f"{key}: SQL has {placeholders} placeholder(s) but {supplied} parameter(s) "
            f"were supplied\n\n{call['sql']}"
        )


@pytest.mark.parametrize("key", REPORT_KEYS)
def test_org_id_is_bound_never_interpolated(key, captured_sql):
    """Tenant isolation: the org must arrive as a bound parameter."""
    defs.REPORTS[key]({}, ORG, "")
    for call in captured_sql:
        assert str(ORG) not in call["sql"], (
            f"{key}: organization id appears literally in the SQL text — it must be "
            f"passed as a parameter\n\n{call['sql']}"
        )
        assert ORG in call["params"], f"{key}: org_id was never bound"
        assert call["org_id"] == ORG


@pytest.mark.parametrize("key", REPORT_KEYS)
def test_every_report_query_is_read_only(key, captured_sql):
    defs.REPORTS[key]({}, ORG, "")
    for call in captured_sql:
        assert_read_only(call["sql"])  # raises on any write keyword


@pytest.mark.parametrize("key", REPORT_KEYS)
def test_every_report_filters_soft_deleted_rows(key, captured_sql):
    """Ensure report queries filter voided/cancelled status on income/expense."""
    defs.REPORTS[key]({}, ORG, "")
    for call in captured_sql:
        sql = call["sql"]
        if re.search(r"\b(income|expense)\b", sql, re.I):
            assert "st.value" in sql or "status_type" in sql, (
                f"{key} reads income/expense without status filter (e.g. excluding cancelled/voided)"
            )


@pytest.mark.parametrize("key", REPORT_KEYS)
def test_every_report_is_row_bounded(key, captured_sql):
    """An unbounded report can return the whole ledger into a prompt."""
    defs.REPORTS[key]({}, ORG, "")
    for call in captured_sql:
        sql = call["sql"].upper()
        bounded = "LIMIT" in sql or "SUM(" in sql or "GROUP  BY MONTH" in sql
        assert bounded, f"{key} has neither a LIMIT nor an aggregate\n\n{call['sql']}"


# ── Ranked reports: count and direction ─────────────────────────────────────
# These 8 report keys rank rows by a value column and take a caller-supplied
# `limit`/`sort_order`. Two bugs shipped from the same root cause here: (1) a
# hardcoded LIMIT that ignored the model-supplied count entirely (so "top 5"
# silently returned 50 rows), and (2) even once count was fixed, every
# ORDER BY was hardcoded DESC with no way to ever honor "bottom N"/"least".
# These pin both down directly against the emitted SQL and bound params.
RANKED_REPORT_KEYS = [
    "rpt_aged_receivables_detail",
    "rpt_aged_payables_detail",
    "rpt_bills_by_contact",
    "rpt_expenses_by_contact",
    "rpt_supplier_statement",
    "rpt_profit_loss_by_project",
    "rpt_profit_loss_by_cost_center",
    "rpt_sales_by_project",
]


@pytest.mark.parametrize("key", RANKED_REPORT_KEYS)
def test_ranked_report_honors_a_caller_supplied_limit(key, captured_sql):
    """'top 5' must come back as exactly 5, never a hardcoded/default row count."""
    defs.REPORTS[key]({"limit": 5}, ORG, "")
    assert captured_sql, f"{key} issued no query"
    for call in captured_sql:
        assert "LIMIT  %s" in call["sql"] or "LIMIT %s" in call["sql"], (
            f"{key}: LIMIT is not parameterised — a caller-supplied count can't reach it\n\n{call['sql']}"
        )
        assert 5 in call["params"], f"{key}: limit=5 was never bound as a query parameter"


@pytest.mark.parametrize("key", RANKED_REPORT_KEYS)
def test_ranked_report_clamps_an_out_of_range_limit(key, captured_sql):
    defs.REPORTS[key]({"limit": 9999}, ORG, "")
    for call in captured_sql:
        assert 50 in call["params"], f"{key}: an oversized limit must clamp to the ceiling (50)"


@pytest.mark.parametrize("key", RANKED_REPORT_KEYS)
def test_ranked_report_defaults_to_descending(key, captured_sql):
    defs.REPORTS[key]({}, ORG, "")
    for call in captured_sql:
        assert re.search(r"ORDER\s+BY\s+\S.*\bDESC\b", call["sql"], re.IGNORECASE), (
            f"{key}: default direction should be DESC (top/highest) when unspecified\n\n{call['sql']}"
        )


@pytest.mark.parametrize("key", RANKED_REPORT_KEYS)
def test_ranked_report_honors_ascending_sort_order(key, captured_sql):
    """'bottom N' / 'least' must actually flip the SQL to ASC, not just relabel a DESC slice."""
    defs.REPORTS[key]({"sort_order": "asc"}, ORG, "")
    for call in captured_sql:
        assert re.search(r"ORDER\s+BY\s+\S.*\bASC\b", call["sql"], re.IGNORECASE), (
            f"{key}: sort_order='asc' did not produce an ASC ordering\n\n{call['sql']}"
        )
        assert not re.search(r"ORDER\s+BY\s+\S.*\bDESC\b", call["sql"], re.IGNORECASE), (
            f"{key}: sort_order='asc' still produced a DESC ordering\n\n{call['sql']}"
        )


# ── Return shape ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", REPORT_KEYS)
def test_report_returns_narratable_shape(key, captured_sql):
    """The narrator keys on `summary` plus a row collection."""
    out = defs.REPORTS[key]({}, ORG, "")
    assert isinstance(out, dict)
    assert out.get("report"), f"{key} has no report title"
    assert isinstance(out.get("summary"), dict) and out["summary"], f"{key} has no summary"


def test_estimate_conversion_rate_is_computed():
    with patch.object(defs, "query", return_value=[
        {"status": "ACCEPTED", "count": 3, "total_amount": 300.0},
        {"status": "PENDING", "count": 1, "total_amount": 100.0},
    ]):
        out = defs.estimate_conversion({}, ORG, "")
    assert out["summary"]["total_estimates"] == 4
    assert out["summary"]["converted"] == 3
    assert out["summary"]["conversion_rate_pct"] == 75.0


def test_estimate_conversion_handles_no_estimates():
    """Division by zero on an empty period must not raise."""
    with patch.object(defs, "query", return_value=[]):
        out = defs.estimate_conversion({}, ORG, "")
    assert out["summary"]["conversion_rate_pct"] == 0.0


def test_vat_export_return_nets_output_against_input():
    with patch.object(defs, "query", side_effect=[
        [{"standard_rated_sales": 1000.0, "output_vat": 50.0}],
        [{"standard_rated_purchases": 400.0, "input_vat": 20.0}],
    ]):
        out = defs.vat_export_return({}, ORG, "")
    assert out["summary"]["net_vat_payable"] == 30.0


def test_vat_export_return_survives_empty_result_sets():
    with patch.object(defs, "query", side_effect=[[], []]):
        out = defs.vat_export_return({}, ORG, "")
    assert out["summary"]["net_vat_payable"] == 0.0


# ── Parameter handling ───────────────────────────────────────────────────────

def test_limit_is_bounded_against_model_supplied_values():
    """`limit` reaches us from model output, so it must be clamped."""
    assert defs._limit({"limit": 5}) == 5
    assert defs._limit({"limit": 9999}) == 50
    assert defs._limit({"limit": -3}) == 1
    assert defs._limit({"limit": "not a number"}) == 20
    assert defs._limit({}) == 20


def test_as_of_is_never_in_the_future():
    """dates.resolve() has no 'today' phrase and falls through to 31 December."""
    from gemini_brain.router.dates import today as org_today

    resolved = ReportAsOfParams().to_query(RequestCtx(org_id=ORG))["as_of_date"]
    assert resolved == org_today().isoformat(), (
        "an as-of date in the future inflates days_overdue by up to a year"
    )


def test_period_params_resolve_to_a_date_range():
    q = ReportPeriodParams(period="last quarter").to_query(RequestCtx(org_id=ORG))
    assert q["organization_id"] == ORG
    assert q["start_date"] < q["end_date"]


def test_limit_params_reject_out_of_range_values():
    with pytest.raises(Exception):
        ReportPeriodLimitParams(period="this year", limit=500)


def test_parse_date_falls_back_on_junk():
    import datetime
    fallback = datetime.date(2026, 1, 1)
    assert parse_date(None, fallback) == "2026-01-01"
    assert parse_date("not-a-date", fallback) == "2026-01-01"
    assert parse_date("2025-06-15", fallback) == "2025-06-15"


def test_total_of_ignores_nulls_and_booleans():
    rows = [{"x": 1.5}, {"x": None}, {"x": "junk"}, {"x": True}, {"x": 2.5}]
    assert total_of(rows, "x") == 4.0


def test_serialize_rows_makes_decimals_and_dates_json_safe():
    import datetime
    from decimal import Decimal
    out = serialize_rows([{"a": Decimal("1.25"), "b": datetime.date(2026, 3, 1), "c": "x"}])
    assert out == [{"a": 1.25, "b": "2026-03-01", "c": "x"}]


# ── Safe execution wrapper ───────────────────────────────────────────────────

def test_unknown_report_returns_invalid_not_an_exception():
    res = run_report_safe("rpt_does_not_exist", {}, ORG)
    assert res.outcome is Outcome.INVALID
    assert res.reason == "unknown_report"


def test_report_failure_is_captured_as_an_outcome():
    with patch.dict(defs.REPORTS, {"rpt_boom": lambda p, o, d: (_ for _ in ()).throw(RuntimeError("db down"))}):
        res = run_report_safe("rpt_boom", {}, ORG)
    assert res.outcome in (Outcome.INVALID, Outcome.UNAVAILABLE)
    assert res.tier == "sql_report"


def test_successful_report_is_classified_usable():
    with patch.dict(defs.REPORTS, {"rpt_ok": lambda p, o, d: {"report": "R", "summary": {"n": 1}, "rows": [{"a": 1}]}}):
        res = run_report_safe("rpt_ok", {}, ORG)
    assert res.outcome in (Outcome.OK, Outcome.PARTIAL)


def test_caller_params_are_not_mutated():
    original = {"start_date": "2026-01-01"}
    with patch.dict(defs.REPORTS, {"rpt_ok": lambda p, o, d: p.update({"injected": True}) or {"summary": {"n": 1}}}):
        run_report_safe("rpt_ok", original, ORG)
    assert "injected" not in original


# ── Engine safety statements ─────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, description=None, rows=()):
        self.description = description
        self._rows = list(rows)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = True
        self.rolled_back = 0
        self.committed = 0
        self.closed = 0

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.rolled_back += 1

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed += 1


def _run_query_against(cursor, monkeypatch, sql="SELECT 1 WHERE organization_id = %s"):
    from gemini_brain.reports import engine as eng
    conn = FakeConn(cursor)
    monkeypatch.setattr(eng, "get_connection", lambda db_name="": conn)
    rows = eng.query(sql, (ORG,), ORG, "")
    return conn, rows


def test_query_sets_read_only_rls_and_timeout_in_order(monkeypatch):
    cursor = FakeCursor(description=[("a",)], rows=[(1,)])
    conn, rows = _run_query_against(cursor, monkeypatch)

    issued = [sql for sql, _ in cursor.executed]
    assert "SET TRANSACTION READ ONLY;" in issued[0], "read-only must be set before anything runs"
    assert any("app.current_org" in s for s in issued), "RLS org context was never set"
    assert any("statement_timeout" in s for s in issued), "no statement timeout"
    assert conn.autocommit is False
    assert rows == [{"a": 1}]


def test_query_binds_the_org_to_the_rls_setting(monkeypatch):
    cursor = FakeCursor(description=[("a",)], rows=[(1,)])
    _run_query_against(cursor, monkeypatch)
    rls = [params for sql, params in cursor.executed if "app.current_org" in sql]
    assert rls == [(str(ORG),)]


def test_query_rejects_a_write_before_opening_a_connection(monkeypatch):
    from gemini_brain.reports import engine as eng
    opened = []
    monkeypatch.setattr(eng, "get_connection", lambda db_name="": opened.append(1))
    with pytest.raises(ValueError):
        eng.query("DELETE FROM income", (), ORG, "")
    assert not opened, "a write statement must be refused before a connection is opened"


def test_query_always_closes_the_connection(monkeypatch):
    cursor = FakeCursor(description=[("a",)], rows=[(1,)])
    conn, _ = _run_query_against(cursor, monkeypatch)
    assert conn.closed == 1

    class Boom(FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("query exploded")

    from gemini_brain.reports import engine as eng
    bad = FakeConn(Boom())
    monkeypatch.setattr(eng, "get_connection", lambda db_name="": bad)
    with pytest.raises(RuntimeError):
        eng.query("SELECT 1", (), ORG, "")
    assert bad.closed == 1
    assert bad.rolled_back >= 1
