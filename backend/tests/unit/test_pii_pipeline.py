"""
test_pii_pipeline.py — The agent redacts personal data before the question and the thread reach Bedrock.
"""
import json

from gemini_brain.agent import loop


class _Adapter:
    """Records what would be sent to Bedrock and answers without calling a tool."""

    sent = []

    def __init__(self, model_id, label=""):
        self.model_id = model_id

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        _Adapter.sent.append([dict(m) for m in messages])
        return {"stopReason": "end_turn",
                "output": {"message": {"role": "assistant", "content": [{"text": "Noted."}]}}}

    def get_token_usage(self):
        return {"llm_calls": 1, "cost_usd": 0.0}


def test_question_and_history_are_redacted_before_bedrock(monkeypatch):
    monkeypatch.setattr(loop, "BedrockAdapter", _Adapter)
    monkeypatch.setattr(loop.law, "answer", lambda question, messages, brief=False: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [])
    _Adapter.sent = []
    history = [{"role": "user", "content": "I am jane.roe@acme.com, call me on +971 55 765 4321"},
               {"role": "assistant", "content": "Hello Jane, how can I help?"}]

    loop.run_agent("My email is john.doe@acme.com and my phone is +971 50 123 4567, please check revenue for 2026.",
                   [69], {69: {"name": "Org", "currency": "AED"}}, history=history)

    sent = json.dumps(_Adapter.sent)
    assert _Adapter.sent, "the agent never called the model"
    for raw in ("john.doe@acme.com", "+971 50 123 4567", "jane.roe@acme.com", "+971 55 765 4321"):
        assert raw not in sent
    assert "[EMAIL_REDACTED]" in sent
    assert "[PHONE_REDACTED]" in sent
