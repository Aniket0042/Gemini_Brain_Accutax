"""
test_resilience_matrix.py — Comprehensive Failure Injection & Resilience Matrix Test Suite (§13).
Validates all 12 operational failure & recovery scenarios.
"""
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved
from gemini_brain.resilience.errors import ErrorCode, AppError, classify_exception
from gemini_brain.resilience.messages import notice_for
from gemini_brain.resilience.envelope import normalize_envelope


def _make_mock_runner():
    with patch("gemini_brain.orchestrator.gemini_brain_runner.settings") as mock_settings:
        mock_settings.gemini_api_key = "dummy"
        mock_settings.aws_region = "us-east-1"
        mock_settings.aws_access_key_id = "dummy"
        mock_settings.aws_secret_access_key = "dummy"
        mock_settings.accutax_base_url = "http://dummy"
        mock_settings.accutax_auth_token = "dummy"
        runner = GeminiBrainRunner(api_key="test-api-key")
        runner._call_llm = MagicMock(return_value=('{"intent": 3, "reason": "data query"}', 10, 5))
        return runner


# ── Scenario 1: Live API returns 200 with data ───────────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
@patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data")
def test_scenario_1_live_api_200_ok(mock_reason, mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/income/list", "path_params": {}, "query_params": {}}, 10, 5)
    mock_reason.return_value = ("Total income is AED 50,000.00", "Claude Haiku 4.5", 100, 30)

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.OK,
        tier="live_api",
        endpoint="/income/list",
        payload=[{"id": 1, "amount": 50000}],
        row_count=1,
    ))

    res = runner.run("what is my income", organization_id=1)
    assert res["status"] == "ok"
    assert res["notice"] is None
    assert res["data_source"]["tier"] == "live_api"
    assert res["data_source"]["endpoint"] == "/income/list"
    assert res["results"] == [{"id": 1, "amount": 50000}]
    assert "AED 50,000.00" in res["answer"]


# ── Scenario 2: Live API returns 200 with empty list [] ───────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_2_live_api_empty_list(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/invoice/list", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.EMPTY,
        tier="live_api",
        endpoint="/invoice/list",
        payload=[],
        row_count=0,
    ))
    runner._db_fallback = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data", side_effect=Exception("bedrock down")) as mock_reason:
        res = runner.run("show overdue invoices", organization_id=1)
        assert res["status"] == "empty"
        assert res["notice"]["code"] == "NO_ROWS"
        assert res["notice"]["kind"] == "empty"
        assert res["results"] == []
        assert "no matching records" in res["answer"].lower()
        # Narration is attempted (with one retry) even for zero rows; this falls back
        # to the deterministic answer above only because Bedrock is unavailable here.
        assert mock_reason.call_count == 2
        assert runner._db_fallback.call_count == 0


# ── Scenario 3: Live API returns 404 ─────────────────────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner._verify_empty_via_sql", return_value=None)
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_3_live_api_404_not_found(mock_intent, mock_sel, mock_verify):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/ar-aging-summary", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.EMPTY,
        tier="live_api",
        endpoint="/report/ar-aging-summary",
        reason="http_404",
        http_status=404,
    ))

    res = runner.run("show aging summary", organization_id=1)
    assert res["status"] == "empty"
    assert res["notice"]["code"] == "NO_ROWS"
    assert res["results"] == []


# ── Scenario 4: Live API returns 401/403 ─────────────────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_4_live_api_403_denied(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/balance-sheet", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.DENIED,
        tier="live_api",
        endpoint="/report/balance-sheet",
        http_status=403,
    ))
    runner._db_fallback = MagicMock()

    res = runner.run("show balance sheet", organization_id=1)
    assert res["status"] in ("degraded", "failed")
    assert res["error"] == "TENANT_FORBIDDEN"
    assert res["notice"]["code"] == "TENANT_FORBIDDEN"
    assert runner._db_fallback.call_count == 0


# ── Scenario 5: Live API returns 503 / timeout -> SQL Fallback ────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_5_live_api_503_triggers_sql_fallback(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/expense/list", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.UNAVAILABLE,
        tier="live_api",
        endpoint="/expense/list",
        reason="http_503",
    ))
    runner._db_fallback = MagicMock(return_value={
        "answer": "Expenses total AED 12,000.00",
        "sql": "SELECT * FROM fn_expenses(1)",
        "results": [{"total": 12000}],
        "error": None,
        "token_usage": {"input_tokens": 10, "output_tokens": 5, "llm_calls": 1},
        "agent_trace": [],
    })

    res = runner.run("show expenses", organization_id=1)
    assert runner._db_fallback.call_count == 1
    assert res["status"] == "ok"
    assert res["results"] == [{"total": 12000}]


# ── Scenario 6: SQL Fallback Succeeds ─────────────────────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_6_sql_fallback_succeeds(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": None}, 10, 5)

    runner = _make_mock_runner()
    runner._db_fallback = MagicMock(return_value={
        "answer": "Found 3 transactions",
        "sql": "SELECT * FROM transactions WHERE organization_id = 1",
        "results": [{"id": 1}, {"id": 2}, {"id": 3}],
        "error": None,
        "token_usage": {"input_tokens": 50, "output_tokens": 20, "llm_calls": 1},
        "agent_trace": [],
    })

    res = runner.run("custom complex query", organization_id=1)
    assert res["status"] == "ok"
    assert len(res["results"]) == 3
    assert res["sql"] is not None


# ── Scenario 7: SQL Fallback Fails ────────────────────────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_7_sql_fallback_fails(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": None}, 10, 5)

    runner = _make_mock_runner()
    runner._db_fallback = MagicMock(return_value={
        "answer": "Error: relation does not exist",
        "sql": "SELECT * FROM non_existent_table",
        "results": [],
        "error": "relation does not exist",
        "token_usage": {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0},
        "agent_trace": [],
    })

    res = runner.run("query broken table", organization_id=1)
    assert res["status"] == "degraded"
    assert res["notice"]["code"] == "SQL_FALLBACK_FAILED"
    assert res["results"] == []


# ── Scenario 8: Bedrock Anthropic Rate Limited (429) ──────────────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
@patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data")
def test_scenario_8_bedrock_rate_limited(mock_reason, mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "data query"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/items/list", "path_params": {}, "query_params": {}}, 10, 5)
    mock_reason.side_effect = Exception("429 Too Many Requests (Rate limit reached)")

    runner = _make_mock_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.OK,
        tier="live_api",
        endpoint="/items/list",
        payload=[{"id": 1, "name": "Item A"}],
        row_count=1,
    ))

    res = runner.run("list items", organization_id=1)
    # Bedrock outage degrades to the formatted table -- it does not fail the query
    assert res["status"] == "partial"
    assert res["notice"]["code"] == "MODEL_UNAVAILABLE"
    assert res["answer"]


# ── Scenario 9: DB Connection Failure ─────────────────────────────────────────
def test_scenario_9_db_connection_failure():
    from gemini_brain.sql_fallback.db_connection import execute_sql_function_safe
    with patch("gemini_brain.sql_fallback.db_connection.execute_sql_function") as mock_exec:
        mock_exec.side_effect = Exception("psycopg2.OperationalError: could not connect to server: Connection refused")
        ret = execute_sql_function_safe("fn_test", (1,), 1)
        assert ret.outcome is Outcome.UNAVAILABLE
        assert ret.reason == "db_unavailable"


# ── Scenario 10: Direct Answer (LEFT path) Provider Failure ───────────────────
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_scenario_10_direct_answer_failure(mock_intent):
    mock_intent.return_value = ({"type": 1, "reason": "app guidance"}, 10, 5)

    runner = _make_mock_runner()
    runner._call_llm = MagicMock(side_effect=Exception("Bedrock primary call down"))

    with patch("gemini_brain.orchestrator.gemini_brain_runner.BedrockAdapter") as mock_bedrock_cls:
        mock_adapter = MagicMock()
        mock_adapter.converse.side_effect = Exception("Bedrock API down")
        mock_bedrock_cls.return_value = mock_adapter

        res = runner.run("how to record journal", organization_id=1)
        assert res["status"] in ("degraded", "failed")
        assert res["results"] == []
        assert "Bedrock primary call down" not in res["answer"]
        assert "Bedrock API down" not in res["answer"]


# ── Scenario 11: Malformed Request Payload ────────────────────────────────────
def test_scenario_11_malformed_request_validation():
    from gemini_brain.resilience.envelope import normalize_envelope
    empty_norm = normalize_envelope({})
    assert empty_norm["status"] == "ok"
    assert empty_norm["results"] == []
    assert isinstance(empty_norm["answer"], str)
    assert isinstance(empty_norm["request_id"], str) and len(empty_norm["request_id"]) > 0


# ── Scenario 12: Uncaught Exception in Runner ─────────────────────────────────
def test_scenario_12_uncaught_exception_envelope():
    runner = _make_mock_runner()
    err_res = runner._err("Unexpected division by zero in internal module", 0, code=ErrorCode.INTERNAL_ERROR)
    assert err_res["status"] in ("degraded", "failed")
    assert err_res["notice"]["code"] == "INTERNAL_ERROR"
    assert "division by zero" not in err_res["answer"]
    assert err_res["results"] == []
    assert len(err_res.get("request_id", "")) > 0 or len(err_res["notice"].get("request_id", "")) > 0
