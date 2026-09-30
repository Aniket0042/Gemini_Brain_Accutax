"""Multi-org invoice list uses an org-filtered report; the comparison is told the real currencies."""
from __future__ import annotations

from unittest.mock import patch

from gemini_brain.observability.timing import QueryTrace
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.orchestrator.multi_org import OrgRun, currency_note, merge_org_results
from gemini_brain.reports import definitions
from gemini_brain.resilience.outcomes import Outcome, Retrieved


# ── rpt_invoice_list ─────────────────────────────────────────────────────────

def _capture_invoice_list(params, rows=None, totals=None):
    calls = []

    def fake_query(sql, args, org_id, db_name=""):
        calls.append((" ".join(sql.split()), args, org_id))
        if "COUNT(*)" in sql:
            return totals if totals is not None else [{"invoice_count": 2, "total_invoiced": 300, "total_outstanding": 100}]
        return rows if rows is not None else [{"id": 1, "total": 200, "amount_paid": 200, "balance": 0}]

    with patch.object(definitions, "query", fake_query):
        result = definitions.invoice_list(params, 4, "")
    return result, calls


def test_invoice_list_filters_by_org_and_period():
    result, calls = _capture_invoice_list({"start_date": "2026-01-01", "end_date": "2026-12-31"})
    rows_sql, rows_args, org = calls[0]
    assert "inc.organization_id = %s" in rows_sql
    assert rows_args[:3] == (4, "2026-01-01", "2026-12-31") and org == 4
    assert "user_id" not in rows_sql
    assert result["period"] == {"start_date": "2026-01-01", "end_date": "2026-12-31"}


def test_invoice_list_summary_covers_whole_period_not_only_listed_rows():
    result, _ = _capture_invoice_list({})
    assert result["summary"] == {
        "invoice_count": 2, "total_invoiced": 300.0, "total_outstanding": 100.0, "listed": 1,
    }


def test_invoice_list_maps_unpaid_status():
    _, calls = _capture_invoice_list({"status": "unpaid"})
    sql, args, _ = calls[0]
    assert "st.value IN %s" in sql
    assert ("PENDING", "PARTIALLY_PAID") in args


def test_invoice_list_all_status_adds_no_filter():
    _, calls = _capture_invoice_list({"status": "all"})
    assert "st.value IN" not in calls[0][0]


def test_invoice_list_limit_is_bounded():
    _, calls = _capture_invoice_list({"limit": 100000})
    assert calls[0][1][-1] == 200


def test_invoice_list_is_registered_as_org_scoped_substitute():
    assert definitions.ORG_SCOPED_SUBSTITUTES["/income/list"] == "rpt_invoice_list"
    assert definitions.REPORTS["rpt_invoice_list"] is definitions.invoice_list


# ── _retrieve uses the substitute only in multi-org runs ─────────────────────

def _retrieve(org_scoped_only: bool):
    runner = GeminiBrainRunner(api_key="test-key")
    runner._org_scoped_rest_only = org_scoped_only
    sel = {"endpoint": "/income/list", "path_params": {}, "query_params": {"start_date": "2026-01-01"}}
    api_ok = Retrieved(Outcome.OK, payload=[{"id": 9}], tier="live_api", endpoint="/income/list")
    report_ok = Retrieved(Outcome.OK, payload={"invoices": [{"id": 1}]}, tier="sql_report", endpoint="rpt_invoice_list")
    with patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.get_sync", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.set_sync"), \
         patch("gemini_brain.config.accutax_openapi.path_exists", return_value=True), \
         patch("gemini_brain.config.accutax_openapi.query_param_names", return_value={"userId", "start_date"}), \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=api_ok) as api, \
         patch("gemini_brain.reports.engine.run_report_safe", return_value=report_ok) as report:
        res = runner._retrieve(sel, 4, "", QueryTrace(org_id=4), auth_token="t")
    return res, api, report


def test_multi_org_invoice_list_comes_from_org_filtered_report():
    res, api, report = _retrieve(org_scoped_only=True)
    api.assert_not_called()
    assert report.call_args.args[0] == "rpt_invoice_list"
    assert report.call_args.args[2] == 4
    assert res.endpoint == "rpt_invoice_list" and res.tier == "sql_report_fallback"


def test_single_org_invoice_list_still_uses_rest_and_no_report():
    res, api, report = _retrieve(org_scoped_only=False)
    api.assert_called_once()
    report.assert_not_called()
    assert res.endpoint == "/income/list"


# ── Currency note ────────────────────────────────────────────────────────────

def _run(name, currency):
    return OrgRun(1, name, currency, result={"status": "ok", "answer": "a", "results": []})


def test_same_currency_note_says_amounts_can_be_compared():
    note = currency_note([_run("A", "AED"), _run("B", "AED")])
    assert "all organizations report in AED" in note and "may be compared" in note


def test_different_currency_note_names_each_currency():
    note = currency_note([_run("A", "AED"), _run("B", "USD")])
    assert "currencies differ: A (AED), B (USD)" in note


def test_unknown_currency_note_forbids_combining():
    note = currency_note([_run("A", "AED"), _run("B", "")])
    assert "unknown for B" in note


class _Compare:
    def _call_llm(self, system, user_text, **kwargs):
        _Compare.seen = user_text
        return "ok", 1, 1


def test_comparison_prompt_carries_the_currency_note():
    merge_org_results("revenue", [_run("A", "AED"), _run("B", "AED")], _Compare())
    assert "Currency note: all organizations report in AED" in _Compare.seen


# ── Result cache is not shared between single-org and multi-org runs ─────────

def _retrieve_with_cache(store: dict, org_scoped_only: bool):
    runner = GeminiBrainRunner(api_key="test-key")
    runner._org_scoped_rest_only = org_scoped_only
    sel = {"endpoint": "/income/list", "path_params": {}, "query_params": {"start_date": "2026-01-01"}}
    api_ok = Retrieved(Outcome.OK, payload=[{"id": "rest-all-orgs"}], tier="live_api", endpoint="/income/list")
    report_ok = Retrieved(Outcome.OK, payload={"invoices": [{"id": "report-org-4"}]}, tier="sql_report", endpoint="rpt_invoice_list")
    with patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.get_sync", side_effect=store.get), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.set_sync",
               side_effect=lambda k, v, ttl=0: store.__setitem__(k, v)), \
         patch("gemini_brain.config.accutax_openapi.path_exists", return_value=True), \
         patch("gemini_brain.config.accutax_openapi.query_param_names", return_value={"userId", "start_date"}), \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=api_ok) as api, \
         patch("gemini_brain.reports.engine.run_report_safe", return_value=report_ok) as report:
        res = runner._retrieve(sel, 4, "", QueryTrace(org_id=4), auth_token="t")
    return res, api.called, report.called


def test_single_org_does_not_get_multi_org_cached_report():
    store: dict = {}
    _retrieve_with_cache(store, org_scoped_only=True)
    res, api_called, _ = _retrieve_with_cache(store, org_scoped_only=False)
    assert api_called
    assert res.payload == [{"id": "rest-all-orgs"}]


def test_multi_org_does_not_get_single_org_cached_cross_org_rest_data():
    store: dict = {}
    _retrieve_with_cache(store, org_scoped_only=False)
    res, api_called, report_called = _retrieve_with_cache(store, org_scoped_only=True)
    assert not api_called and report_called
    assert res.payload == {"invoices": [{"id": "report-org-4"}]}
