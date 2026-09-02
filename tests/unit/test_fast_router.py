"""
test_fast_router.py — Unit tests for deterministic fast router and runner integration.
"""
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.observability import METRICS
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience import Outcome, Retrieved
from gemini_brain.router.fast_router import FastRouteResult, fast_route


def test_fast_router_income_total():
    """income_total resolves to a direct DB report, not the REST /income/total
    endpoint -- that endpoint was found to ignore organization_id and return
    the identical figure for every org, including ones that don't exist."""
    res = fast_route("What is our total revenue this year?", organization_id=27, user_id="18")
    assert res is not None
    assert res.endpoint == "rpt_income_total"
    assert res.intent == 4
    assert res.query_params["organization_id"] == 27
    assert "start_date" in res.query_params
    assert "end_date" in res.query_params


def test_fast_router_expense_total():
    res = fast_route("Show total expenses for 2026", organization_id=27, user_id="18")
    assert res is not None
    assert res.endpoint == "/expense/total"
    assert res.query_params["filter_year"] == "2026"


def test_fast_router_profit_loss():
    res = fast_route("Show profit and loss statement for this month", organization_id=27)
    assert res is not None
    assert res.endpoint == "/report/profit-loss"
    assert res.intent == 3
    assert "start_date" in res.query_params
    assert "end_date" in res.query_params


def test_fast_router_balance_sheet():
    res = fast_route("Show Balance Sheet as of today", organization_id=27)
    assert res is not None
    assert res.endpoint == "/report/balance-sheet"
    assert "as_of_date" in res.query_params


def test_fast_router_cash_forecast():
    res = fast_route("Show expected cash flow projection for next month", organization_id=27)
    assert res is not None
    assert res.endpoint == "/report/cash-forecast"
    assert res.intent == 5
    assert res.query_params["months"] == 6


def test_fast_router_ar_aging():
    res = fast_route("Who owes us overdue invoices aging report", organization_id=27)
    assert res is not None
    assert res.endpoint == "/report/ar-aging-summary"
    assert "as_of_date" in res.query_params


def test_fast_router_customer_balance():
    res = fast_route("Show customer balances and outstanding receivable", organization_id=27)
    assert res is not None
    assert res.endpoint == "/report/customer-balance-summary"


def test_fast_router_bank_accounts():
    res = fast_route("What is our bank balance and cash balance?", organization_id=27)
    assert res is not None
    assert res.endpoint == "/bank/manual/accounts"


def test_fast_router_unrecognized_returns_none():
    res = fast_route("How do I create a recurring invoice in Accutax?", organization_id=27)
    assert res is None


def test_fast_router_runner_integration_zero_gemini_calls():
    """Verify that a fast-router hit executes with 0 LLM routing calls.

    income_total is routed to a direct DB report (rpt_income_total), not REST --
    see test_fast_router_income_total for why. run_report_safe is what
    _retrieve() actually calls for rpt_ endpoints, so that's what's mocked here.
    """
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=27)
    runner._call_llm = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent") as mock_classify, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint") as mock_select, \
         patch("gemini_brain.reports.engine.run_report_safe", return_value=Retrieved(Outcome.OK, payload={"summary": {"total_income": 50000.00}}, row_count=1, endpoint="rpt_income_total")) as mock_run_report, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data", return_value=("Total sales is AED 50,000.00", "Claude Haiku 4.5", 10, 10)) as mock_reason:

        res = runner.run(
            query="What is our total sales this year?",
            organization_id=27,
            use_api=True,
        )

        # LLM classification and selection must NOT be called
        mock_classify.assert_not_called()
        mock_select.assert_not_called()
        runner._call_llm.assert_not_called()

        # DB report and Claude reasoning are executed directly
        mock_run_report.assert_called_once()
        mock_reason.assert_called_once()

        assert res["routing_info"]["path"] == "api_then_anthropic"
        assert res["routing_info"]["api_endpoint"] == "rpt_income_total"
