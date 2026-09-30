"""Every REST call to Accutax carries the verified organization, whatever the selection wrote."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.api_client.accutax_client import force_org_params
from gemini_brain.observability.timing import QueryTrace
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved


# ── force_org_params ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["organization_id", "organizationId", "orgId", "org_id"])
def test_other_org_in_query_is_overwritten(key):
    _, query = force_org_params({}, {key: 6, "year": "2026"}, 4)
    assert query == {key: "4", "year": "2026"}


def test_other_org_in_path_is_overwritten():
    path, _ = force_org_params({"organizationId": "6", "id": "12"}, {}, 4)
    assert path == {"organizationId": "4", "id": "12"}


def test_missing_org_added_when_spec_accepts_it():
    _, query = force_org_params({}, {"year": "2026"}, 4, {"organizationId", "year"})
    assert query == {"year": "2026", "organizationId": "4"}


def test_nothing_added_without_spec():
    _, query = force_org_params({}, {"year": "2026"}, 4, None)
    assert query == {"year": "2026"}


def test_nothing_added_when_endpoint_takes_no_org():
    _, query = force_org_params({}, {"code": "AED"}, 4, {"code"})
    assert query == {"code": "AED"}


def test_inputs_are_not_mutated():
    query_in = {"organization_id": 6}
    force_org_params({}, query_in, 4)
    assert query_in == {"organization_id": 6}


# ── _retrieve sends the verified org ─────────────────────────────────────────

def _sent_params(sel: dict, org_id: int, exists, accepted):
    """Run one REST retrieval and return the (path, query) params sent over HTTP."""
    runner = GeminiBrainRunner(api_key="test-key")
    ok = Retrieved(Outcome.OK, payload={"total": 1}, tier="live_api", endpoint=sel["endpoint"])
    with patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.get_sync", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.set_sync"), \
         patch("gemini_brain.config.accutax_openapi.path_exists", return_value=exists), \
         patch("gemini_brain.config.accutax_openapi.query_param_names", return_value=accepted), \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=ok) as call:
        runner._retrieve(sel, org_id, "", QueryTrace(org_id=org_id), auth_token="t")
    args = call.call_args.args
    return args[1], args[2]


def test_model_chosen_sibling_org_is_replaced_before_the_call():
    sel = {"endpoint": "/report/profit-loss", "path_params": {}, "query_params": {"organization_id": "6"}}
    _, query = _sent_params(sel, 4, True, {"organization_id", "start_date"})
    assert query["organization_id"] == "4"


def test_org_the_model_forgot_is_added_before_the_call():
    sel = {"endpoint": "/report/profit-loss", "path_params": {}, "query_params": {"start_date": "2026-01-01"}}
    _, query = _sent_params(sel, 4, True, {"organization_id", "start_date"})
    assert query == {"start_date": "2026-01-01", "organization_id": "4"}


def test_camel_case_org_is_replaced_even_without_spec():
    sel = {"endpoint": "/accounting/journal-entries", "path_params": {}, "query_params": {"organizationId": 6}}
    _, query = _sent_params(sel, 4, None, None)
    assert query["organizationId"] == "4"


# ── Multi-org runs skip REST endpoints with no org parameter ─────────────────

def _retrieve(sel: dict, exists, accepted, org_scoped_only: bool):
    """Run one retrieval; return (result, whether HTTP was called)."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._org_scoped_rest_only = org_scoped_only
    ok = Retrieved(Outcome.OK, payload={"rows": [1]}, tier="live_api", endpoint=sel["endpoint"])
    with patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.get_sync", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.result_cache.set_sync"), \
         patch("gemini_brain.config.accutax_openapi.path_exists", return_value=exists), \
         patch("gemini_brain.config.accutax_openapi.query_param_names", return_value=accepted), \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=ok) as call:
        res = runner._retrieve(sel, 4, "", QueryTrace(org_id=4), auth_token="t")
    return res, call.called


INCOME_LIST = {"endpoint": "/income/list", "path_params": {}, "query_params": {"userId": "7"}}
INCOME_LIST_PARAMS = {"userId", "page", "pageSize", "start_date", "end_date"}


def test_multi_org_run_skips_endpoint_without_org_param():
    res, called = _retrieve(INCOME_LIST, True, INCOME_LIST_PARAMS, org_scoped_only=True)
    assert not called
    assert res.outcome is Outcome.UNAVAILABLE and res.reason == "not_org_scoped"


def test_single_org_run_still_calls_endpoint_without_org_param():
    res, called = _retrieve(INCOME_LIST, True, INCOME_LIST_PARAMS, org_scoped_only=False)
    assert called and res.outcome is Outcome.OK


def test_multi_org_run_calls_endpoint_with_org_param():
    sel = {"endpoint": "/report/profit-loss", "path_params": {}, "query_params": {}}
    res, called = _retrieve(sel, True, {"organization_id", "start_date"}, org_scoped_only=True)
    assert called and res.outcome is Outcome.OK


def test_multi_org_run_skips_known_unscoped_endpoint_without_spec():
    sel = {"endpoint": "/income/total", "path_params": {}, "query_params": {}}
    res, called = _retrieve(sel, None, None, org_scoped_only=True)
    assert not called and res.reason == "not_org_scoped"


def test_multi_org_run_calls_other_endpoint_without_spec():
    sel = {"endpoint": "/report/profit-loss", "path_params": {}, "query_params": {}}
    _, called = _retrieve(sel, None, None, org_scoped_only=True)
    assert called


def test_run_leaves_the_block_off_by_default():
    runner = GeminiBrainRunner(api_key="test-key")
    with patch.object(runner, "_run_inner", return_value={"answer": "x"}), \
         patch.object(runner, "_apply_delivery", side_effect=lambda r, **k: r), \
         patch.object(runner, "_decorate", side_effect=lambda r, *a, **k: r):
        runner.run(query="list invoices", organization_id=4)
    assert runner._org_scoped_rest_only is False
