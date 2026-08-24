"""
test_llm_router.py — Unit tests for Gemini Structured Function Calling router and catalog declarations.
"""
from unittest.mock import MagicMock
import pytest

from gemini_brain.endpoints.endpoint_selector import select_endpoint
from gemini_brain.router.llm_router import (
    ToolCallResult,
    parse_function_call,
    route_with_gemini,
    select_endpoint_structured,
)
from gemini_brain.tools.context import RequestCtx
from gemini_brain.tools.registry import REGISTRY, gemini_declarations
from gemini_brain.tools.schemas import (
    BankRulesParams,
    ContactListParams,
    IncomeTotalParams,
    ProfitLossParams,
    TrialBalanceParams,
    VatSummaryParams,
)


def test_gemini_declarations_coverage():
    """Verify gemini_declarations generates valid OpenAPI/JSON function schemas for all registered tools."""
    decls = gemini_declarations()
    assert len(decls) == len(REGISTRY)
    assert len(decls) >= 40

    names = {d["name"] for d in decls}
    assert "profit_loss" in names
    assert "balance_sheet" in names
    assert "income_total" in names
    assert "trial_balance" in names
    assert "contact_list" in names
    assert "vat_summary" in names
    assert "answer_directly" in names
    assert "unsupported" in names

    for d in decls:
        assert "name" in d
        assert "description" in d
        assert "parameters" in d
        assert d["parameters"]["type"] == "OBJECT"
        assert "properties" in d["parameters"]


def test_parse_function_call_structures():
    """Test extracting function name and parameters across Gemini response formats."""
    # Format 1: native dict with functionCall
    r1 = {"functionCall": {"name": "profit_loss", "args": {"period": "2026"}}}
    name, args = parse_function_call(r1)
    assert name == "profit_loss"
    assert args == {"period": "2026"}

    # Format 2: raw JSON string with name & parameters
    r2 = '{"name": "income_total", "parameters": {"period": "this quarter"}}'
    name, args = parse_function_call(r2)
    assert name == "income_total"
    assert args == {"period": "this quarter"}

    # Format 3: raw JSON string with tool & params
    r3 = '{"tool": "trial_balance", "params": {"period": "last year"}}'
    name, args = parse_function_call(r3)
    assert name == "trial_balance"
    assert args == {"period": "last year"}

    # Format 4: invalid / plain string
    r4 = "Sorry, I could not find a tool."
    name, args = parse_function_call(r4)
    assert name == "unsupported"


def test_route_with_gemini_success():
    """Verify route_with_gemini calls Gemini caller and returns ToolCallResult."""
    mock_caller = MagicMock(return_value=('{"name": "profit_loss", "parameters": {"period": "2026"}}', 50, 15))
    res = route_with_gemini("Show P&L for 2026", gemini_caller=mock_caller)

    assert isinstance(res, ToolCallResult)
    assert res.name == "profit_loss"
    assert res.params == {"period": "2026"}
    assert res.tokens_in == 50
    assert res.tokens_out == 15


def test_select_endpoint_structured_pydantic_mapping():
    """Verify select_endpoint_structured maps tool calls into verified query and path parameters."""
    mock_caller = MagicMock(return_value=('{"name": "income_total", "parameters": {"period": "2026"}}', 40, 10))
    sel, ti, to = select_endpoint_structured(
        query="What is total revenue in 2026?",
        org_id=27,
        call_gemini=mock_caller,
        user_id="18",
    )

    assert sel is not None
    assert sel["endpoint"] == "/income/total"
    assert sel["tool_name"] == "income_total"
    assert sel["intent"] == 4
    assert sel["query_params"]["filter_year"] == "2026"
    assert sel["query_params"]["filter_type"] == "YEARLY"
    assert sel["query_params"]["organization_id"] == 27
    assert sel["query_params"]["user_id"] == "18"


def test_select_endpoint_structured_trial_balance():
    """Verify structured selection for newly registered Trial Balance report."""
    mock_caller = MagicMock(return_value=('{"name": "trial_balance", "parameters": {"period": "this year"}}', 35, 10))
    sel, ti, to = select_endpoint_structured(
        query="Show trial balance report",
        org_id=27,
        call_gemini=mock_caller,
        user_id="18",
    )

    assert sel is not None
    assert sel["endpoint"] == "/report/trial-balance"
    assert sel["intent"] == 3
    assert "start_date" in sel["query_params"]
    assert "end_date" in sel["query_params"]
    assert sel["query_params"]["organization_id"] == 27


def test_select_endpoint_structured_contact_list():
    """Verify structured selection for customer and vendor contact filtering."""
    ctx = RequestCtx(org_id=27, user_id="18")

    # Customer filter -> contact_type_id=4
    c_params = ContactListParams(contact_type="customer")
    q = c_params.to_query(ctx)
    assert q["contact_type_id"] == 4

    # Vendor filter -> contact_type_id=5
    v_params = ContactListParams(contact_type="vendor")
    q = v_params.to_query(ctx)
    assert q["contact_type_id"] == 5


def test_select_endpoint_integration_with_keyword_fallback():
    """Verify select_endpoint falls back to keyword fallback when structured call returns unsupported."""
    mock_caller = MagicMock(return_value=('{"name": "unsupported", "parameters": {}}', 20, 5))
    sel, ti, to = select_endpoint(
        query="show total sales for this year",
        org_id=27,
        call_gemini=mock_caller,
        user_id="18",
    )

    assert sel is not None
    assert sel["endpoint"] == "/income/total"
    assert "filter_year" in sel["query_params"]
