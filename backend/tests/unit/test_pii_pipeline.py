"""
test_pii_pipeline.py — Integration test verifying PII redaction choke point in GeminiBrainRunner.
"""
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved


def test_pipeline_redaction_e2e():
    """Verify raw PII query is redacted before reaching external LLM calls."""
    raw_query = "My email is john.doe@acme.com and my phone is +971 50 123 4567, please check custom analytics for 2026."

    runner = GeminiBrainRunner(api_key="mock_key")

    # Mocks for downstream external LLM calls
    mock_gemini = MagicMock(return_value=('{"type": 4, "reason": "data_query"}', 10, 10))
    runner._call_llm = mock_gemini

    mock_resolve_org = MagicMock(return_value=69)
    runner._resolve_organization = mock_resolve_org

    mock_select_endpoint = MagicMock(
        return_value=({"endpoint": "/income/total", "query_params": {}}, 10, 10)
    )

    mock_judge = MagicMock(return_value=("SIMPLE", 10, 10))

    mock_reason = MagicMock(
        return_value=("Total sales is AED 100,000.00", "Claude Haiku 4.5", 15, 15)
    )

    mock_save_msg = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent", return_value=({"type": 4, "reason": "data_query"}, 10, 10)) as mock_classify, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint", return_value=({"endpoint": "/income/total", "query_params": {}}, 10, 10)) as mock_endpoint_sel, \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=Retrieved(Outcome.OK, payload={"total": 100000.00}, tier="live_api", endpoint="/income/total")), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data", return_value=("Total sales is AED 100,000.00", "Claude Haiku 4.5", 15, 15)) as mock_reasoner, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.save_message_by_session", mock_save_msg), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.update_conversation_state_hybrid_by_session"), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.maybe_auto_title"):

        res = runner.run(query=raw_query, organization_id=69, session_id="5c2bb4d7-4a00-4bf4-9c38-bdcd7e537ade")

        # 1. Verify classify_intent received redacted query
        classified_query = mock_classify.call_args[0][0]
        assert "[EMAIL_REDACTED]" in classified_query
        assert "[PHONE_REDACTED]" in classified_query
        assert "john.doe@acme.com" not in classified_query
        assert "+971 50 123 4567" not in classified_query

        # 2. Verify select_endpoint received redacted query
        endpoint_query = mock_endpoint_sel.call_args[0][0]
        assert "[EMAIL_REDACTED]" in endpoint_query
        assert "[PHONE_REDACTED]" in endpoint_query

        # 3. Verify reason_over_data received redacted query
        reasoner_query = mock_reasoner.call_args[1]["query"]
        assert "[EMAIL_REDACTED]" in reasoner_query
        assert "[PHONE_REDACTED]" in reasoner_query

        # 4. Verify save_message_by_session stored the RAW unredacted query for local history
        save_msg_user_call = [c for c in mock_save_msg.call_args_list if c[0][1] == "user"]
        assert len(save_msg_user_call) > 0
        saved_user_query = save_msg_user_call[0][0][2]
        assert saved_user_query == raw_query
        assert "john.doe@acme.com" in saved_user_query
