"""Links from agent answers into the Accutax app: built in code from tool results only."""
import pytest

from gemini_brain.agent import links, loop, preview


@pytest.fixture(autouse=True)
def _app(monkeypatch):
    monkeypatch.setattr(links.settings, "accutax_app_url", "http://app.test/")


def _docs(kind, rows):
    return {"tool": "list_documents", "result": {"type": kind, "rows": rows}}


DATA = [
    _docs("sales_invoices", [{"document_number": "INV-0012", "document_id": "501"},
                             {"document_number": "INV-001", "document_id": 500}]),
    _docs("bills", [{"document_number": "BILL-7", "document_id": 77}]),
    _docs("journal_lines", [{"journal_number": "JE-24-2026-00000773", "journal_id": "9001"}]),
]


def test_each_document_type_links_to_its_record_page():
    assert links.document_links(DATA) == {
        "INV-0012": "http://app.test/income/details/501",
        "INV-001": "http://app.test/income/details/500",
        "BILL-7": "http://app.test/expenses/edit-expense/77",
        "JE-24-2026-00000773": "http://app.test/journal-entries/9001",
    }


def test_numbers_in_text_and_tables_become_links_without_touching_longer_numbers():
    text = "Largest: INV-0012 and INV-001.\n\n| Doc | Amount |\n|---|---|\n| BILL-7 | 5 |\n| INV-0013 | 2 |"
    out = links.link_documents(text, links.document_links(DATA))
    assert "[INV-0012](http://app.test/income/details/501)" in out
    assert "[INV-001](http://app.test/income/details/500)." in out
    assert "| [BILL-7](http://app.test/expenses/edit-expense/77) |" in out
    assert "| INV-0013 |" in out


def test_existing_links_and_code_are_left_alone():
    text = "See [INV-001](http://x) and `INV-001`, then INV-001."
    out = links.link_documents(text, {"INV-001": "http://app.test/income/details/500"})
    assert out == "See [INV-001](http://x) and `INV-001`, then [INV-001](http://app.test/income/details/500)."


def test_a_number_shared_by_two_records_gets_no_link():
    data = [_docs("sales_invoices", [{"document_number": "INV-1", "document_id": 1},
                                     {"document_number": "INV-1", "document_id": 2}])]
    assert links.document_links(data) == {}


def test_no_app_url_means_no_links(monkeypatch):
    monkeypatch.setattr(links.settings, "accutax_app_url", "")
    assert links.document_links(DATA) == {}


def test_guide_buttons_once_per_page():
    data = [{"tool": "app_guide", "result": {"app_url": "http://app.test/create-new/invoice"}},
            {"tool": "app_guide", "result": {"app_url": "http://app.test/create-new/invoice"}},
            {"tool": "app_guide", "result": {"status": "no_match", "app_url": None}}]
    assert links.guide_buttons(data) == [{"type": "action_button", "label": "Open in Accutax",
                                          "url": "http://app.test/create-new/invoice"}]


def test_preview_answers_carry_document_links_and_the_guide_button(monkeypatch):
    data = DATA + [{"tool": "app_guide", "result": {"app_url": "http://app.test/journal-entries"}}]
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(
        answer="Top invoice INV-0012; journal JE-24-2026-00000773.", status="ok", data=data))
    out = preview.answer("Show my largest invoice", [24], lambda: {}, session_id=None, user_id=1)
    assert "[INV-0012](http://app.test/income/details/501)" in out["answer"]
    assert "[JE-24-2026-00000773](http://app.test/journal-entries/9001)" in out["answer"]
    assert {"type": "action_button", "label": "Open in Accutax", "url": "http://app.test/journal-entries"} in out["blocks"]




def test_payments_link_by_direction_and_settlements_link_both_numbers():
    data = [
        _docs("payments", [{"payment_number": "SPY-2026-0537", "payment_id": 811, "direction": "paid"},
                           {"payment_number": "CPY-2026-0001", "payment_id": "12", "direction": "received"}]),
        _docs("payment_settlements", [{"payment_number": "SPY-2026-0540", "payment_id": 815, "direction": "paid",
                                       "document_number": "EXP-77", "document_id": 77},
                                      {"payment_number": "CPY-2026-0002", "payment_id": 13, "direction": "received",
                                       "document_number": "INV-9", "document_id": 9}]),
    ]
    assert links.document_links(data) == {
        "SPY-2026-0537": "http://app.test/edit-supplier-payment/811",
        "CPY-2026-0001": "http://app.test/edit-customer-payment/12",
        "SPY-2026-0540": "http://app.test/edit-supplier-payment/815",
        "EXP-77": "http://app.test/expenses/edit-expense/77",
        "CPY-2026-0002": "http://app.test/edit-customer-payment/13",
        "INV-9": "http://app.test/income/details/9",
    }


def test_a_reused_payment_number_gets_no_link():
    data = [_docs("payments", [{"payment_number": "SPY-2026-0572", "payment_id": 1, "direction": "paid"},
                               {"payment_number": "SPY-2026-0572", "payment_id": 2, "direction": "paid"}])]
    assert links.document_links(data) == {}
