"""
test_self_correction.py — Unit tests for Phase E bounded 1-turn self-correction loop.
"""
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience import Outcome, Retrieved
from gemini_brain.router.llm_router import select_endpoint_structured


def test_llm_router_injects_feedback_into_prompt():
    """Verify that feedback is added to Gemini system prompt on correction attempt."""
    def dummy_gemini(system_prompt, user_message, max_tokens=250, thinking_budget=0):
        assert "PREVIOUS ATTEMPT FAILED / CORRECTION FEEDBACK:" in system_prompt
        assert "Endpoint '/item/find' failed" in system_prompt
        return '{"name": "item_list", "parameters": {}}', 40, 15

    sel, ti, to = select_endpoint_structured(
        query="show inventory items",
        org_id=27,
        call_gemini=dummy_gemini,
        feedback="Endpoint '/item/find' failed with 404.",
    )
    assert sel is not None
    assert sel["endpoint"] == "/item/list"
    assert ti == 40
    assert to == 15


def test_runner_recovers_via_1_turn_self_correction():
    """Verify runner catches initial invalid/failed retrieval, re-selects with feedback, and recovers."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=27)
    runner._call_llm = MagicMock()

    call_count = 0

    def mock_select(query, org_id, call_gemini, parse_json=None, user_id="", session_state=None, feedback=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            assert feedback is None
            return {"endpoint": "/item/find", "query_params": {}}, 10, 10
        else:
            assert feedback is not None
            assert "/item/find" in feedback
            return {"endpoint": "/item/list", "query_params": {}}, 15, 10

    # Initial retrieval returns INVALID (e.g. 404), second returns OK with items
    retrieve_count = 0
    def mock_retrieve(sel, org_id, db_name, trace, **kwargs):
        nonlocal retrieve_count
        retrieve_count += 1
        if retrieve_count == 1:
            return Retrieved(Outcome.INVALID, endpoint="/item/find", reason="http_404")
        else:
            return Retrieved(
                Outcome.OK,
                payload=[{"item_name": "Widget A", "unit_price": 50}],
                row_count=1,
                endpoint="/item/list",
            )

    with patch("gemini_brain.orchestrator.gemini_brain_runner.fast_route", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent", return_value=({"type": 4, "reason": "data_query"}, 10, 10)), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint", side_effect=mock_select), \
         patch.object(runner, "_retrieve", side_effect=mock_retrieve), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data", return_value=("Widget A is available.", "Claude Haiku 4.5", 10, 10)):

        res = runner.run(
            query="list items in catalog",
            organization_id=27,
            use_api=True,
        )

        assert call_count == 2
        assert retrieve_count == 2
        assert res["routing_info"]["api_endpoint"] == "/item/list"
        assert "Widget A" in res["answer"]


def test_runner_bounded_retry_does_not_loop():
    """Verify runner only attempts self-correction once; if retry also fails, it enters DB fallback."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=27)
    runner._call_llm = MagicMock()

    select_calls = []
    def mock_select(query, org_id, call_gemini, parse_json=None, user_id="", session_state=None, feedback=None):
        select_calls.append(feedback)
        return {"endpoint": "/broken/endpoint", "query_params": {}}, 10, 10

    def mock_retrieve(sel, org_id, db_name, trace, **kwargs):
        return Retrieved(Outcome.INVALID, endpoint="/broken/endpoint", reason="http_500")

    mock_db_fallback = MagicMock(return_value={
        "status": "success",
        "answer": "Answered via DB fallback",
        "routing_info": {"path": "sql_fallback"},
    })

    with patch("gemini_brain.orchestrator.gemini_brain_runner.fast_route", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent", return_value=({"type": 4, "reason": "data_query"}, 10, 10)), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint", side_effect=mock_select), \
         patch.object(runner, "_retrieve", side_effect=mock_retrieve), \
         patch.object(runner, "_db_fallback", mock_db_fallback):

        res = runner.run(
            query="custom query needing fallback",
            organization_id=27,
            use_api=True,
        )

        # Must have attempted initial select + exactly 1 correction select
        assert len(select_calls) == 2
        assert select_calls[0] is None
        assert select_calls[1] is not None
        mock_db_fallback.assert_called_once()


def test_empty_and_denied_do_not_trigger_self_correction():
    """Verify Outcome.EMPTY (0 rows found) and Outcome.DENIED do not trigger self-correction."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=27)
    runner._call_llm = MagicMock()

    select_calls = []
    def mock_select(query, org_id, call_gemini, parse_json=None, user_id="", session_state=None, feedback=None):
        select_calls.append(feedback)
        return {"endpoint": "/income/list", "query_params": {}}, 10, 10

    with patch("gemini_brain.orchestrator.gemini_brain_runner.fast_route", return_value=None), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent", return_value=({"type": 4, "reason": "data_query"}, 10, 10)), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint", side_effect=mock_select), \
         patch.object(runner, "_retrieve", return_value=Retrieved(Outcome.EMPTY, endpoint="/income/list", reason="zero_rows")), \
         patch("gemini_brain.orchestrator.gemini_brain_runner._verify_empty_via_sql", return_value=None):
        # _verify_empty_via_sql reaches a real DB connection outside of the
        # mocked runner._retrieve above. With a live database configured (org 27
        # genuinely has data in the current DB), that live cross-check can flip
        # the EMPTY outcome this test is asserting on to OK -- unrelated to what
        # this test checks (self-correction gating), so it's forced off here.

        res = runner.run(
            query="show invoices for unknown client",
            organization_id=27,
            use_api=True,
        )

        # Only 1 initial selection, 0 retries
        assert len(select_calls) == 1
        assert res["status"] == "empty"
