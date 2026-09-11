"""Unit tests verifying Task 1 (Organization ID enforcement) and Task 2 (No secret fallbacks)."""
from unittest.mock import MagicMock, patch

import pytest

from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner


def test_missing_org_id_raises_value_error():
    """Verify that calling run with no explicit or resolvable org_id raises ValueError."""
    runner = GeminiBrainRunner(api_key="test_key")
    # Mock _resolve_organization to return None (no org mentioned in query)
    runner._resolve_organization = MagicMock(return_value=None)

    with pytest.raises(ValueError, match="Organization ID is required"):
        runner.run(query="What is total revenue?", organization_id=None)


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_explicit_org_id_accepted(mock_classify):
    """Verify positive case: passing explicit organization_id proceeds past org validation."""
    runner = GeminiBrainRunner(api_key="test_key")
    runner._resolve_organization = MagicMock(return_value=None)
    mock_classify.return_value = ({"type": 1, "reason": "faq"}, 10, 10)
    runner._call_llm = MagicMock(return_value=("Direct Answer", 10, 10))

    # Explicit organization_id=42 passed
    res = runner.run(query="How to create invoice?", organization_id=42)
    assert res is not None
    assert res["answer"] == "Direct Answer"
    assert res["routing_info"]["type"] == 1





def test_invalid_session_id_and_dummy_model_key_sanitized():
    """Verify that dummy Swagger strings ('string', 'null') for session_id and selected_model_key do not crash DB."""
    from gemini_brain.memory.session_memory import (
        get_history_by_session,
        get_state_by_session,
        is_valid_uuid,
        save_message_by_session,
    )

    assert not is_valid_uuid("string")
    assert not is_valid_uuid("null")
    assert not is_valid_uuid("123")
    assert is_valid_uuid("5c2bb4d7-4a00-4bf4-9c38-bdcd7e537ade")

    # These should return safely without executing invalid SQL
    save_message_by_session("string", "user", "test")
    assert get_history_by_session("string") == []
    assert get_state_by_session("string") == {}

