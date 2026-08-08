"""Unit tests for keyword fallback mapping."""
import datetime
from gemini_brain.endpoints.keyword_fallback import keyword_endpoint_fallback


def test_keyword_fallback_cash_forecast():
    today = datetime.date(2026, 8, 5)
    res = keyword_endpoint_fallback("Show cash forecast", 199, today, user_id="18")
    assert res is not None
    assert res["endpoint"] == "/report/cash-forecast"


def test_keyword_fallback_total_sales():
    today = datetime.date(2026, 8, 5)
    res = keyword_endpoint_fallback("What is total sales?", 199, today, user_id="18")
    assert res is not None
    assert res["endpoint"] == "/income/total"
    assert res["query_params"]["filter_type"] == "YEARLY"
