"""Unit tests for Phase 2: Orchestrator outcome branching."""
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved
from gemini_brain.resilience.errors import ErrorCode


def _make_runner():
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


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_empty_outcome_does_not_call_llm_or_sql_fallback(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/invoice/list", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.EMPTY,
        tier="live_api",
        endpoint="/invoice/list",
        payload=[],
        row_count=0,
    ))
    runner._db_fallback = MagicMock()

    with patch(
        "gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
        return_value=("You have no overdue invoices right now.", "Claude Haiku 4.5", 10, 5),
    ) as mock_reason:
        res = runner.run("show me overdue invoices", organization_id=1)

        assert res["status"] == "empty"
        assert res["notice"] is not None
        assert res["notice"]["kind"] == "empty"
        assert res["notice"]["code"] == "NO_ROWS"
        assert res["results"] == []
        assert res["answer"] == "You have no overdue invoices right now."
        assert runner._db_fallback.call_count == 0
        # Zero rows is a confirmed answer, not a failure -- but it is still narrated.
        assert mock_reason.call_count == 1


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_empty_outcome_falls_back_to_deterministic_answer_if_narration_fails(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/invoice/list", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.EMPTY,
        tier="live_api",
        endpoint="/invoice/list",
        payload=[],
        row_count=0,
    ))
    runner._db_fallback = MagicMock()

    with patch(
        "gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
        side_effect=Exception("bedrock down"),
    ):
        res = runner.run("show me overdue invoices", organization_id=1)

        assert res["status"] == "empty"
        assert res["notice"]["code"] == "NO_ROWS"
        assert res["results"] == []
        assert isinstance(res["answer"], str)
        assert len(res["answer"]) > 0
        assert runner._db_fallback.call_count == 0


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_denied_outcome_returns_degraded_without_fallback(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch bank accounts"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/bank/accounts", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.DENIED,
        tier="live_api",
        endpoint="/bank/accounts",
        http_status=403,
    ))
    runner._db_fallback = MagicMock()

    res = runner.run("show bank accounts", organization_id=1)

    assert res["status"] in ("degraded", "failed")
    assert res["error"] == "TENANT_FORBIDDEN"
    assert res["notice"]["code"] == "TENANT_FORBIDDEN"
    assert res["results"] == []
    assert runner._db_fallback.call_count == 0


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_unavailable_outcome_triggers_sql_fallback(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch profit loss"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/profit-loss", "path_params": {}, "query_params": {}}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.UNAVAILABLE,
        tier="live_api",
        endpoint="/report/profit-loss",
        reason="timeout",
    ))
    runner._db_fallback = MagicMock(return_value={
        "answer": "Net profit is 50,000 AED",
        "sql": "SELECT * FROM fn_profit_loss(1)",
        "results": [{"net_profit": 50000}],
        "error": None,
        "token_usage": {"input_tokens": 100, "output_tokens": 50, "llm_calls": 1},
        "agent_trace": [],
    })

    res = runner.run("show profit loss", organization_id=1)

    assert runner._db_fallback.call_count == 1
    assert res["status"] == "ok"
    assert res["results"] == [{"net_profit": 50000}]
    assert "50,000" in res["answer"]


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
@patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data")
def test_partial_outcome_sets_partial_status_and_narrates(mock_reason, mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch ledger"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/general-ledger", "path_params": {}, "query_params": {}}, 10, 5)
    mock_reason.return_value = ("Ledger summary for first 100 entries", "Claude Haiku 4.5", 200, 50)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=Retrieved(
        Outcome.PARTIAL,
        tier="live_api",
        endpoint="/report/general-ledger",
        payload=[{"id": i} for i in range(100)],
        row_count=100,
        truncated=True,
    ))

    res = runner.run("show general ledger", organization_id=1)

    assert res["status"] == "partial"
    assert res["notice"] is not None
    assert res["notice"]["code"] == "PARTIAL_DATA"
    assert len(res["results"]) == 100
    assert mock_reason.call_count == 1
