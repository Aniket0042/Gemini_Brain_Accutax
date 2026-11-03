"""A chart or file request carries into follow-ups that reshape the answer (plan step 1.3)."""
import pytest

from gemini_brain.agent import preview, prompt
from gemini_brain.artifacts.delivery import as_state, carry_delivery, detect_delivery

EXCEL = as_state(detect_delivery("Download revenue by month this year as Excel"))
CHART = as_state(detect_delivery("Chart net sales by month this year"))


@pytest.mark.parametrize("question", [
    "add net profit to it", "same for last year", "now by quarter", "and last quarter?", "make it quarterly",
    "only the top 5", "drop Org1 and redo", "what about last year", "show it by customer instead",
])
def test_follow_ups_keep_the_previous_delivery(question):
    assert carry_delivery(question, EXCEL).format == "xlsx"
    assert carry_delivery(question, CHART).wants_chart


@pytest.mark.parametrize("question", [
    "List the last 5 invoices", "What is VAT?", "Who are our top 5 customers by sales this year?",
    "What is our cash balance?",
    "and also tell me which of the organizations had the largest growth in revenue compared with last year overall",
])
def test_new_questions_do_not(question):
    assert carry_delivery(question, EXCEL).mode == "none"


def test_a_named_delivery_wins_and_no_previous_means_none():
    assert carry_delivery("same for last year as PDF", EXCEL).format == "pdf"
    assert carry_delivery("add net profit to it", None).mode == "none"
    assert carry_delivery("add net profit to it", {"mode": "none"}).mode == "none"


def test_preview_tells_the_model_about_the_carried_file():
    thread = preview.Thread(delivery=EXCEL)
    delivery = preview.delivery_for("add net profit to it", thread)
    assert preview.attachment("add net profit to it", delivery) == "XLSX file"
    assert preview.attachment("add net profit to it") is None  # without the thread


def test_files_are_built_with_the_carried_delivery(monkeypatch):
    seen = {}
    import gemini_brain.artifacts.attach as attach
    monkeypatch.setattr(attach, "attach_delivery", lambda *a, **k: seen.update(k) or [{"type": "canvas"}])

    class Result:
        data = [{"tool": "query_metrics", "input": {}, "result": {"rows": [{"pnl.revenue": "1"}]}}]

    delivery = preview.delivery_for("add net profit to it", preview.Thread(delivery=EXCEL))
    blocks = preview._files("add net profit to it", Result(), [24], {}, user_id=1, session_id=None,
                            answer_text="x", delivery=delivery)
    assert blocks == [{"type": "canvas"}] and seen["delivery"].format == "xlsx"


def test_prompt_asks_when_unclear_and_never_fakes_a_correction():
    rules = prompt.RULES
    assert '"fix it"' in rules and "call no tools" in rules
    assert "Never say figures were corrected" in rules
    assert "Never guess it." in rules


def test_named_formats_lists_every_format_in_priority_order():
    from gemini_brain.artifacts.delivery import named_formats
    assert named_formats("month-end pack as PDF and Excel") == ["xlsx", "pdf"]
    assert named_formats("revenue this year") == []
