"""Unit tests for keyword fallback mapping."""
import datetime
from gemini_brain.endpoints.keyword_fallback import keyword_endpoint_fallback


def test_keyword_fallback_cash_forecast():
    today = datetime.date(2026, 8, 5)
    res = keyword_endpoint_fallback("Show cash forecast", 199, today, user_id="18")
    assert res is not None
    assert res["endpoint"] == "/report/cash-forecast"


def test_keyword_fallback_total_sales():
    """Routed to a direct DB report (rpt_income_total), not REST /income/total --
    that endpoint ignores organization_id and returns the same figure for every
    org, including ones that don't exist."""
    today = datetime.date(2026, 8, 5)
    res = keyword_endpoint_fallback("What is total sales?", 199, today, user_id="18")
    assert res is not None
    assert res["endpoint"] == "rpt_income_total"
    assert res["query_params"]["start_date"] == "2026-01-01"
    assert res["query_params"]["end_date"] == "2026-08-05"
