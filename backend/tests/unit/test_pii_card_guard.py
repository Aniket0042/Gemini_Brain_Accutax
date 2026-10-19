"""A card number typed into the chat is removed before the model, the saved chat or any log sees it."""
import json

import pytest

from gemini_brain.agent import answer_log, loop, preview
from gemini_brain.config.settings import settings
from gemini_brain.privacy import sensitive

CARD = "4111 1111 1111 1111"          # a test card number: passes the Luhn check


@pytest.fixture(autouse=True)
def guard_on(monkeypatch):
    monkeypatch.setattr(settings, "pii_card_guard", "on")


def test_a_card_is_removed_in_any_common_spelling():
    for card in (CARD, "4111-1111-1111-1111", "4111111111111111", "5500 0000 0000 0004"):
        text, counts = sensitive.drop(f"My card is {card}, can I pay VAT with it?")
        assert text == "My card is [card number removed], can I pay VAT with it?" and counts == {"CARD": 1}


def test_figures_and_document_numbers_stay():
    for text in ("Revenue AED 1,234,567.89 this year", "TRN 100234567800003", "Invoice INV-20241216180347-114231",
                 "Card ending 4111 1111 1111 1112 failed the check", "Reference 1234567890123"):
        assert sensitive.drop(text) == (text, {})


def test_the_browser_marker_counts_as_a_dropped_card():
    assert sensitive.drop("Pay with [card number removed]?") == ("Pay with [card number removed]?", {"CARD": 1})


def test_off_leaves_the_question_alone(monkeypatch):
    monkeypatch.setattr(settings, "pii_card_guard", "off")
    assert sensitive.drop(f"card {CARD}") == (f"card {CARD}", {})


def test_the_answer_and_the_screen_say_it_was_removed():
    assert sensitive.with_answer_line("VAT is 5%.", {"CARD": 1}) == (
        "We removed a card number. Do not share card details here.\n\nVAT is 5%.")
    assert sensitive.with_answer_line("VAT is 5%.", {}) == "VAT is 5%."
    assert sensitive.notice_block({"CARD": 2}) == {"type": "sensitive_notice", "kinds": ["CARD"], "count": 2}
    assert sensitive.notice_block({}) is None


def test_the_card_never_reaches_the_model_or_the_log(monkeypatch, tmp_path):
    log = tmp_path / "answers.jsonl"
    monkeypatch.setattr(answer_log.settings, "agent_answer_log", str(log))
    asked = []

    def fake_agent(question, *a, **k):
        asked.append(question)
        return loop.AgentResult(answer="You cannot pay VAT by card here.", status="ok")

    monkeypatch.setattr(loop, "run_agent", fake_agent)
    response = preview.answer(f"Can I pay VAT with card {CARD}?", [24], lambda: {}, session_id=None, user_id=7)
    assert asked == ["Can I pay VAT with card [card number removed]?"]
    assert response["answer"].startswith("We removed a card number. Do not share card details here.")
    assert response["blocks"][0] == {"type": "sensitive_notice", "kinds": ["CARD"], "count": 1}
    assert CARD not in log.read_text(encoding="utf-8") and CARD not in json.dumps(response)


def test_no_notice_without_a_card(monkeypatch):
    monkeypatch.setattr(answer_log.settings, "agent_answer_log", "")
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="Revenue AED 5.", status="ok"))
    response = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=7)
    assert response["answer"] == "Revenue AED 5." and not any(
        b.get("type") == "sensitive_notice" for b in response["blocks"] or [])
