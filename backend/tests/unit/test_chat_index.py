"""Tests for chat-safe OpenAPI index + lexical tool retrieval."""
from unittest.mock import MagicMock

from gemini_brain.config.accutax_openapi import load_spec_for_tests, reset_cache
from gemini_brain.router.llm_router import route_with_gemini, select_endpoint_structured
from gemini_brain.router.tool_retriever import retrieve_tool_names
from gemini_brain.tools.chat_index import get_index, reset_index, resolve_tool


SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/report/profit-loss": {
            "get": {
                "summary": "Get profit and loss report",
                "x-accutax-chat": {
                    "useFor": ["P&L", "income statement", "revenue chart"],
                    "doNotUseFor": ["cash flow", "balance sheet"],
                },
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "start_date", "in": "query"},
                    {"name": "end_date", "in": "query"},
                ],
            }
        },
        "/report/cash-flow": {
            "get": {
                "summary": "Get cash flow statement",
                "x-accutax-chat": {
                    "useFor": ["cash flow statement"],
                    "doNotUseFor": ["cash forecast"],
                },
            }
        },
        "/report/cash-forecast": {
            "get": {
                "summary": "Get cash forecast",
                "x-accutax-chat": {
                    "useFor": ["cash forecast", "cash runway"],
                    "doNotUseFor": ["historical cash flow statement"],
                },
            }
        },
        "/report/quote-details": {
            "get": {
                "summary": "Get quote details",
                "x-accutax-chat": {
                    "useFor": ["quote details", "list estimates", "outstanding quotes"],
                    "doNotUseFor": ["estimate conversion rate"],
                },
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "start_date", "in": "query"},
                    {"name": "end_date", "in": "query"},
                ],
            }
        },
        "/report/export/pdf": {
            "get": {"summary": "Export PDF"},
        },
        "/expense/list_filter": {
            "get": {"summary": "List expenses"},
        },
    },
}


def setup_function():
    reset_cache()
    load_spec_for_tests(SPEC)
    reset_index()


def teardown_function():
    reset_cache()


def _tool_names_from_call(mock_caller) -> list:
    kwargs = mock_caller.call_args.kwargs
    tools = kwargs.get("tools") or []
    names = []
    for decl in tools:
        spec = decl.get("toolSpec") or decl
        names.append(spec.get("name"))
    return names


def test_index_hides_paths_missing_from_openapi():
    index = get_index()
    assert "profit_loss" in index.by_name
    assert "expense_total" not in index.by_name
    assert "trial_balance" not in index.by_name


def test_index_adds_tagged_openapi_only_report():
    index = get_index()
    assert "report_quote_details" in index.by_name
    spec = index.by_name["report_quote_details"].spec
    assert spec.endpoint == "/report/quote-details"
    assert "quote details" in spec.description.lower()


def test_index_skips_export_pdf():
    assert "/report/export/pdf" not in get_index().by_endpoint


def test_retrieve_pnl_ranks_profit_loss():
    names = retrieve_tool_names("Show P&L for 2026")
    assert names[0] in ("answer_directly", "unsupported")
    assert "profit_loss" in names
    assert names.index("profit_loss") < 8


def test_retrieve_cash_forecast_not_historical_cash_flow():
    names = retrieve_tool_names("projected cash runway next month")
    assert "cash_forecast" in names
    if "cash_flow" in names:
        assert names.index("cash_forecast") < names.index("cash_flow")


def test_retrieve_openapi_only_quotes():
    names = retrieve_tool_names("show outstanding quotes and estimate details")
    assert "report_quote_details" in names


def test_retrieve_keeps_menu_small():
    names = retrieve_tool_names("Show profit and loss income statement for this year")
    assert "answer_directly" in names
    assert "unsupported" in names
    assert len(names) <= 20


def test_route_with_gemini_only_sends_retrieved_tools():
    mock_caller = MagicMock(
        return_value=('{"name": "profit_loss", "parameters": {"period": "2026"}}', 50, 15)
    )
    res = route_with_gemini("Show P&L for 2026", gemini_caller=mock_caller)
    assert res.name == "profit_loss"
    sent = _tool_names_from_call(mock_caller)
    assert "profit_loss" in sent
    assert "answer_directly" in sent
    assert len(sent) < 40


def test_select_openapi_only_tool_builds_endpoint():
    mock_caller = MagicMock(
        return_value=('{"name": "report_quote_details", "parameters": {"period": "this year"}}', 20, 8)
    )
    sel, _, _ = select_endpoint_structured(
        query="list outstanding quotes",
        org_id=27,
        call_gemini=mock_caller,
        user_id="18",
    )
    assert sel is not None
    assert sel["endpoint"] == "/report/quote-details"
    assert sel["query_params"]["organization_id"] == 27
    assert "start_date" in sel["query_params"]


def test_resolve_tool_falls_back_to_registry_for_hidden_http():
    """Phantom tools stay callable if a test/mock names them, but are not offered."""
    spec = resolve_tool("trial_balance")
    assert spec is not None
    assert spec.endpoint == "/report/trial-balance"
    assert "trial_balance" not in get_index().by_name
