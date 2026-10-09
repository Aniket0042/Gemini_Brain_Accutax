"""Answer-shape fixes: chart words, truthful attachment notes, requested list sizes, document-number digits."""
import datetime as dt

import pytest

from gemini_brain.agent import loop, preview
from gemini_brain.agent.prompt import system_prompt
from gemini_brain.artifacts.delivery import detect_delivery
from gemini_brain.policy.verifier import verify_answer


@pytest.mark.parametrize("question", [
    "Revenue, expenses and net profit this year for each organization and show the joined bar for the same",
    "revenue by org as grouped columns", "compare orgs side by side bars", "show me a bar of expenses",
    "net profit bar graph",
])
def test_bar_requests_without_the_word_chart_are_charts(question):
    delivery = detect_delivery(question)
    assert delivery.mode == "chart" and delivery.chart_hint == "bar"


@pytest.mark.parametrize("question", ["List my 30 largest unpaid invoices", "Show the chart of accounts",
                                      "total sales by column", "what is the bar council fee"])
def test_other_questions_are_not_charts(question):
    assert detect_delivery(question).mode == "none"


def test_the_model_is_told_whether_something_is_attached():
    with_chart = system_prompt({}, [24], dt.date(2026, 10, 9), "Asia/Dubai", attachment="chart")
    assert "asks for chart" in with_chart and "say the chart is attached below" in with_chart
    plain = system_prompt({}, [24], dt.date(2026, 10, 9), "Asia/Dubai")
    assert "No chart or file is attached to this answer" in plain


def test_list_rules_honour_a_requested_count_and_full_numbers():
    text = system_prompt({}, [24], dt.date(2026, 10, 9), "Asia/Dubai", brief=True)
    assert "list exactly that many" in text and "never shorten them" in text and "at most 5 columns" in text
    assert "unless the user asked for a number of rows" in text


@pytest.mark.parametrize("question, expected", [
    ("Export revenue by month as PDF", "PDF file"),
    ("show the joined bar for revenue by org", "chart"),
    ("Revenue this year", None),
])
def test_preview_tells_the_agent_what_it_will_attach(monkeypatch, question, expected):
    seen = {}
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: seen.update(k) or loop.AgentResult(answer="x", status="ok"))
    preview.answer(question, [24], lambda: {}, session_id=None, user_id=1)
    assert seen["attachment"] == expected


def test_digits_inside_document_numbers_are_not_unverified_figures():
    results = [{"rows": [{"document_number": "INV-BULK-26-20241216180347-114231", "outstanding": "23679",
                          "document_date": "2026-06-10T00:00:00.000"}]}]
    ok = verify_answer("INV-BULK-26-20241216180347-114231 owes AED 23,679", loop._evidence(results), enforce=False)
    assert ok.to_public()["grounded"] is True
    wrong = verify_answer("INV-BULK-26-20241216180347-114231 owes AED 99,999", loop._evidence(results), enforce=False)
    assert wrong.to_public()["unmatched"] == ["99,999"]
