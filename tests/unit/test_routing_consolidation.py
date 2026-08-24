"""
test_routing_consolidation.py — Unit tests verifying Phase C routing rules consolidation (R04, R05).
"""
import datetime
from unittest.mock import MagicMock
import pytest

from gemini_brain.endpoints.keyword_fallback import keyword_endpoint_fallback
from gemini_brain.router.fast_router import FAST_ROUTER_RULES, fast_route
from gemini_brain.router.rules import (
    ROUTING_RULES,
    RoutingRule,
    get_fast_router_rules,
    get_quick_reference_block,
    get_sql_fast_path_rules,
)
from gemini_brain.sql_fallback.fast_path import _FAST_PATH, try_fast_path


def test_consolidation_rule_count_and_uniqueness():
    """Verify master ROUTING_RULES has unique rule names and valid endpoints."""
    assert len(ROUTING_RULES) >= 20
    names = [r.name for r in ROUTING_RULES]
    assert len(names) == len(set(names)), "Duplicate rule names in ROUTING_RULES"

    for r in ROUTING_RULES:
        assert r.name != ""
        assert len(r.patterns) >= 1
        assert r.endpoint != ""
        assert 1 <= r.intent <= 7


def test_fast_router_rules_derived_from_rules_module():
    """Verify fast_router FAST_ROUTER_RULES is generated directly from rules.py."""
    derived = get_fast_router_rules()
    assert len(FAST_ROUTER_RULES) == len(derived)
    assert len(FAST_ROUTER_RULES) >= 20

    rule_names = {r[1] for r in FAST_ROUTER_RULES}
    assert "income_total" in rule_names
    assert "expense_total" in rule_names
    assert "profit_loss" in rule_names
    assert "trial_balance" in rule_names


def test_sql_fast_path_includes_income_and_expense_total():
    """Verify R05: _FAST_PATH includes income_total and expense_total tasks."""
    task_names = [task for _, task, _ in _FAST_PATH]
    assert "get_invoice_total" in task_names, "Missing get_invoice_total in _FAST_PATH"
    assert "aggregate_metric" in task_names, "Missing aggregate_metric in _FAST_PATH"
    assert "top_customers" in task_names
    assert "profit_and_loss" in task_names


def test_sql_try_fast_path_resolves_total_revenue():
    """Verify try_fast_path successfully matches 'total revenue' style queries (R05)."""
    mock_handler = MagicMock(return_value={"success": True, "data": {"total_revenue": 500000}})
    handlers = {"finance_agent": mock_handler}

    res = try_fast_path("What is our total revenue this year?", org_id=27, agent_handlers=handlers)
    assert res is not None
    data, task, params = res
    assert task == "get_invoice_total"
    assert params["organization_id"] == 27
    assert params["filter_type"] == "YEARLY"
    mock_handler.assert_called_once_with("get_invoice_total", params)


def test_keyword_fallback_matches_consolidated_rules():
    """Verify keyword_endpoint_fallback resolves queries across consolidated rules."""
    today = datetime.date(2026, 8, 20)

    # 1. Total income
    sel_inc = keyword_endpoint_fallback("how much revenue in 2026", org_id=27, today=today)
    assert sel_inc is not None
    assert sel_inc["endpoint"] == "/income/total"

    # 2. P&L
    sel_pnl = keyword_endpoint_fallback("show p&l statement", org_id=27, today=today)
    assert sel_pnl is not None
    assert sel_pnl["endpoint"] == "/report/profit-loss"

    # 3. Cash forecast
    sel_cf = keyword_endpoint_fallback("cash flow forecast", org_id=27, today=today)
    assert sel_cf is not None
    assert sel_cf["endpoint"] == "/report/cash-forecast"


def test_quick_reference_block_generated():
    """Verify get_quick_reference_block generates valid hint lines."""
    block = get_quick_reference_block()
    assert "QUICK REFERENCE" in block
    assert "/income/total" in block
    assert "/report/profit-loss" in block
    assert "/report/balance-sheet" in block
