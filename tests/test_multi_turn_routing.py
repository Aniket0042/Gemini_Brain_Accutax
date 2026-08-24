"""
test_multi_turn_routing.py — Unit tests for Phase D multi-turn context routing.
"""
import datetime
from gemini_brain.router.dates import resolve, today
from gemini_brain.router.fast_router import fast_route
from gemini_brain.classification.intent_classifier import classify_intent
from gemini_brain.router.llm_router import select_endpoint_structured


def test_dates_bare_quarter():
    """Verify bare quarter parsing in dates.resolve."""
    anchor = datetime.date(2026, 8, 20)
    w_q2 = resolve("q2", anchor=anchor)
    assert w_q2.date_from == datetime.date(2026, 4, 1)
    assert w_q2.date_to == datetime.date(2026, 6, 30)

    w_q1 = resolve("quarter 1", anchor=anchor)
    assert w_q1.date_from == datetime.date(2026, 1, 1)
    assert w_q1.date_to == datetime.date(2026, 3, 31)


def test_fast_router_multi_turn_follow_up():
    """Verify fast_router resolves relative follow-up queries using session_state."""
    session_state = {
        "last_executed_task": "/report/profit-loss",
        "active_year": "2026",
    }

    # Turn 2: "What about Q2?"
    res_q2 = fast_route("What about Q2?", organization_id=27, session_state=session_state)
    assert res_q2 is not None
    assert res_q2.endpoint == "/report/profit-loss"
    assert res_q2.query_params["start_date"] == "2026-04-01"
    assert res_q2.query_params["end_date"] == "2026-06-30"

    # Turn 3: "And how does that compare to last year?"
    res_ly = fast_route("And how does that compare to last year?", organization_id=27, session_state=session_state)
    assert res_ly is not None
    assert res_ly.endpoint == "/report/profit-loss"
    assert res_ly.query_params["start_date"] == "2025-01-01"
    assert res_ly.query_params["end_date"] == "2025-12-31"


def test_fast_router_without_session_state_misses_follow_up():
    """Verify relative follow-up queries without session context do not blindly match."""
    res = fast_route("What about Q2?", organization_id=27, session_state=None)
    assert res is None


def test_llm_router_passes_session_state():
    """Verify select_endpoint_structured injects session_state into context."""
    def dummy_gemini(system_prompt, user_message, max_tokens=250, thinking_budget=0):
        assert "ACTIVE CONVERSATION CONTEXT" in system_prompt
        assert "/report/profit-loss" in system_prompt
        return '{"name": "profit_loss", "parameters": {"period": "Q2"}}', 50, 20

    session_state = {
        "last_executed_task": "/report/profit-loss",
        "active_year": "2026",
    }

    sel, ti, to = select_endpoint_structured(
        query="What about Q2?",
        org_id=27,
        call_gemini=dummy_gemini,
        session_state=session_state,
    )
    assert sel is not None
    assert sel["endpoint"] == "/report/profit-loss"
    assert ti == 50
    assert to == 20
