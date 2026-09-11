"""Tests for live Accutax OpenAPI catalog + query-param alignment."""
from gemini_brain.config.accutax_openapi import (
    catalog_text,
    iter_chat_operations,
    load_spec_for_tests,
    normalize_query_params,
    path_exists,
    reset_cache,
)
from gemini_brain.config.api_catalog import get_api_catalog


SAMPLE_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/report/profit-loss": {
            "get": {
                "summary": "Get profit and loss report",
                "x-accutax-chat": {
                    "useFor": ["P&L", "income statement"],
                    "doNotUseFor": ["cash flow"],
                },
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "start_date", "in": "query"},
                    {"name": "end_date", "in": "query"},
                    {"name": "user_id", "in": "query"},
                ],
            }
        },
        "/report/expenses-by-contact": {
            "get": {
                "summary": "Get expenses by contact",
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "min_date", "in": "query"},
                    {"name": "max_date", "in": "query"},
                ],
            }
        },
        "/report/cash-forecast": {
            "get": {
                "summary": "Get cash forecast",
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "start_date", "in": "query"},
                    {"name": "end_date", "in": "query"},
                    {"name": "currency", "in": "query"},
                ],
            }
        },
        "/expense/list_filter": {
            "get": {
                "summary": "List expenses",
                "parameters": [
                    {"name": "organization_id", "in": "query"},
                    {"name": "expense_type", "in": "query", "required": True},
                ],
            }
        },
    },
}


def setup_function():
    reset_cache()
    load_spec_for_tests(SAMPLE_SPEC)


def teardown_function():
    reset_cache()


def test_path_exists_uses_backend_spec():
    assert path_exists("/report/profit-loss") is True
    assert path_exists("/expense/total") is False
    assert path_exists("/report/trial-balance") is False


def test_normalize_drops_undeclared_params_and_aliases_dates():
    out = normalize_query_params(
        "/report/expenses-by-contact",
        {
            "organization_id": 27,
            "start_date": "2026-01-01",
            "end_date": "2026-09-11",
            "limit": 20,
            "sort_order": "desc",
        },
    )
    assert out["min_date"] == "2026-01-01"
    assert out["max_date"] == "2026-09-11"
    assert "limit" not in out
    assert "sort_order" not in out


def test_normalize_strips_months_from_cash_forecast():
    out = normalize_query_params(
        "/report/cash-forecast",
        {
            "organization_id": 27,
            "months": 6,
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
        },
    )
    assert "months" not in out
    assert out["start_date"] == "2026-01-01"


def test_catalog_text_lists_deployed_reports_only():
    text = catalog_text()
    assert "GET /report/profit-loss" in text
    assert "/expense/total" not in text
    assert "from Accutax backend OpenAPI" in text
    assert "USE FOR: P&L" in text
    assert "DO NOT USE FOR: cash flow" in text


def test_get_api_catalog_prefers_live_spec():
    text = get_api_catalog()
    assert "GET /report/profit-loss" in text
    assert "GET /expense/list_filter" in text


def test_iter_chat_operations_reads_vendor_extension():
    ops = {row["path"]: row for row in iter_chat_operations()}
    assert ops["/report/profit-loss"]["tagged"] is True
    assert "P&L" in ops["/report/profit-loss"]["useFor"]
    assert "cash flow" in ops["/report/profit-loss"]["doNotUseFor"]
