"""Accutax reports that ignore organization_id are never called, single-org or multi-org."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.observability.timing import QueryTrace
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.reports.definitions import ORG_IGNORED_REST, REST_TO_SQL_REPORT
from gemini_brain.resilience.outcomes import Outcome, Retrieved

ORG_PARAMS = {"organization_id", "user_id", "start_date", "end_date"}


def _retrieve(endpoint: str, org_scoped_only: bool, report: Retrieved):
    runner = GeminiBrainRunner(api_key="test-key")
    runner._org_scoped_rest_only = org_scoped_only
    sel = {"endpoint": endpoint, "path_params": {}, "query_params": {"start_date": "2026-01-01"}}
    ok = Retrieved(Outcome.OK, payload={"rows": [1]}, tier="live_api", endpoint=endpoint)
    with patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.get_sync", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.set_sync"), \
         patch("gemini_brain.config.accutax_openapi.path_exists", return_value=True), \
         patch("gemini_brain.config.accutax_openapi.query_param_names", return_value=ORG_PARAMS), \
         patch("gemini_brain.reports.engine.run_report_safe", return_value=report) as sql, \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=ok) as http:
        res = runner._retrieve(sel, 4, "", QueryTrace(org_id=4), auth_token="t")
    return res, http.called, sql


@pytest.mark.parametrize("org_scoped_only", [False, True])
@pytest.mark.parametrize("endpoint", sorted(ORG_IGNORED_REST))
def test_never_called_over_http(endpoint, org_scoped_only):
    down = Retrieved(Outcome.UNAVAILABLE, tier="sql_report", reason="db_unavailable")
    res, http_called, _ = _retrieve(endpoint, org_scoped_only, down)
    assert not http_called
    assert res.outcome is Outcome.UNAVAILABLE


@pytest.mark.parametrize("endpoint", sorted(e for e in ORG_IGNORED_REST if e in REST_TO_SQL_REPORT))
def test_project_reports_answer_from_org_filtered_sql(endpoint):
    report = Retrieved(Outcome.OK, payload={"summary": {"total_revenue": 1}}, tier="sql_report")
    res, http_called, sql = _retrieve(endpoint, False, report)
    assert not http_called
    assert res.outcome is Outcome.OK and res.tier == "sql_report_fallback"
    assert sql.call_args.args[0] == REST_TO_SQL_REPORT[endpoint]
    assert sql.call_args.args[2] == 4  # the verified organization


def test_honoured_reports_are_not_blocked():
    for endpoint in ("/report/consolidated-pnl", "/report/supplier-statement-of-account", "/report/profit-loss"):
        assert endpoint not in ORG_IGNORED_REST
