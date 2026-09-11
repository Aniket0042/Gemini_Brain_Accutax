"""Working-memory helpers: meta questions, prompt block, and chat messages."""
from unittest.mock import MagicMock, patch

from gemini_brain.config.constants import NEVER_EXPOSE_BACKEND_RULE
from gemini_brain.classification.intent_classifier import classify_intent
from gemini_brain.memory.conversation_window import (
    append_memory_block,
    format_memory_block,
    is_conversation_meta_query,
    llm_messages_from_memory,
)
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner


def test_conversation_meta_detects_first_query():
    assert is_conversation_meta_query("what was my first query?")
    assert is_conversation_meta_query("What did I just ask?")
    assert is_conversation_meta_query("remind me of my first question")
    assert is_conversation_meta_query("summarize this conversation")


def test_conversation_meta_ignores_accounting_questions():
    assert not is_conversation_meta_query("what was my first invoice?")
    assert not is_conversation_meta_query("show total revenue this year")
    assert not is_conversation_meta_query("how do I create a journal entry?")


def test_format_memory_block_pins_first_user_message():
    block = format_memory_block(
        summary="",
        messages=[{"role": "user", "content": "later question"}, {"role": "assistant", "content": "ok"}],
        first_user_query="Show me cash flow",
    )
    assert "This thread's first user message: Show me cash flow" in block
    assert "User: later question" in block


def test_llm_messages_include_prior_turns_then_current():
    state = {
        "_memory_messages": [
            {"role": "user", "content": "Show me cash flow"},
            {"role": "assistant", "content": "Cash flow is AED 10,000."},
        ]
    }
    messages = llm_messages_from_memory(state, "what was my first query?")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][0]["text"] == "Show me cash flow"
    assert messages[-1]["content"][0]["text"] == "what was my first query?"


def test_append_memory_block_tells_model_to_use_history():
    state = {"_memory_block": "CONVERSATION SO FAR:\nUser: hello"}
    prompt = append_memory_block("You are Accutax AI.", state)
    assert "Never say you lack conversation history" in prompt
    assert "CONVERSATION SO FAR" in prompt


def test_never_expose_rule_allows_recapping_this_chat():
    assert "Never claim you have no conversation history" in NEVER_EXPOSE_BACKEND_RULE
    assert "prior conversation history" not in NEVER_EXPOSE_BACKEND_RULE


def test_classify_parse_fail_on_meta_query_is_faq_not_data():
    def fake_llm(system, user_text, max_tokens):
        return "I don't have access to your conversation history.", 10, 20

    def fake_parse(text, default):
        return default

    result, _, _ = classify_intent(
        "what was my first query?",
        fake_llm,
        fake_parse,
        session_state={"_memory_block": "CONVERSATION SO FAR:\nUser: hello"},
    )
    assert result["type"] == 1
    assert "conversation_meta" in result["reason"]


def test_runner_meta_query_skips_sql_and_sends_history_as_messages():
    """'what was my first query?' must not hit API/SQL, and prior turns must be real messages."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=2)
    runner._persist_conversation = MagicMock()
    captured = {}

    def fake_llm(system="", user_text="", max_tokens=2000, messages=None, **kwargs):
        captured["messages"] = messages
        captured["system"] = system
        return ("Your first query was 'Show cash flow'.", 10, 20)

    runner._call_llm = fake_llm
    history_state = {
        "_memory_block": (
            "CONVERSATION SO FAR:\n"
            "This thread's first user message: Show cash flow\n"
            "User: Show cash flow\n"
            "Assistant: Cash flow is AED 10,000."
        ),
        "_memory_messages": [
            {"role": "user", "content": "Show cash flow"},
            {"role": "assistant", "content": "Cash flow is AED 10,000."},
        ],
        "_first_user_query": "Show cash flow",
    }

    with patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent") as mock_classify, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.fast_route") as mock_fast, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.attach_working_memory", return_value=history_state), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.get_state_by_session", return_value={}), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.get_project_context_by_session", return_value=None):
        res = runner.run(
            query="what was my first query?",
            organization_id=2,
            session_id="5c2bb4d7-4a00-4bf4-9c38-bdcd7e537ade",
            use_api=True,
        )

    mock_classify.assert_not_called()
    mock_fast.assert_not_called()
    assert "Show cash flow" in res["answer"]
    assert [m["role"] for m in captured["messages"]] == ["user", "assistant", "user"]
    assert captured["messages"][0]["content"][0]["text"] == "Show cash flow"
    assert captured["messages"][-1]["content"][0]["text"] == "what was my first query?"
    assert "This thread's first user message: Show cash flow" in captured["system"]
