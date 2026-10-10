"""Unit tests for organization scoping of a chat and for dummy session ids."""
import pytest
from fastapi import HTTPException

from gemini_brain.api import routes
from gemini_brain.api.auth import CurrentUser


def test_a_chat_with_no_organization_is_refused():
    """No org named and none allowed: refused before the agent runs."""
    user = CurrentUser(user_id=1, email="", allowed_org_ids=[])
    with pytest.raises(HTTPException) as err:
        routes._agent_scope(user, [])
    assert err.value.status_code == 403


def test_a_chat_naming_no_organization_runs_on_the_first_allowed_one():
    user = CurrentUser(user_id=1, email="", allowed_org_ids=[42, 43])
    assert routes._agent_scope(user, []) == [42]


def test_named_organizations_are_kept():
    """_query_orgs has already authorized them; the scope never widens them."""
    user = CurrentUser(user_id=1, email="", allowed_org_ids=[42, 43])
    assert routes._agent_scope(user, [43]) == [43]


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

