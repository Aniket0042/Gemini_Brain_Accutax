"""Guessing what a text file is, so the chat offers fitting questions (no model, no figures depend on it)."""
import pytest

from gemini_brain.config.settings import settings
from gemini_brain.uploads import reader, store
from gemini_brain.uploads.kind import guess_type
from gemini_brain.uploads.validate import inspect
from tests.unit import upload_files as f

CONTRACT = ("SERVICE AGREEMENT between the parties. Payment terms: net 30 days. A late payment fee applies. "
            "Governing law: the UAE. Either party may end this agreement by termination notice.")
BUDGET_SHEET = "Line | Q1 | Q2 | Q3 | Q4\nRevenue | 1200000 | 1300000 | 1250000 | 1400000\nCost of sales | 700000 | 760000\nSalaries | 240000"


def test_a_contract_is_known_by_its_words():
    assert guess_type(CONTRACT, "agreement.docx", "docx") == "contract"
    assert guess_type("Payment terms: 30 days. Governing law: UAE.", "x.pdf", "pdf") == "contract"


def test_one_contract_word_is_not_enough():
    assert guess_type("Thank you for your agreement to meet on Friday.", "note.docx", "docx") == "other"


def test_a_budget_sheet_is_known_by_its_rows_and_periods_or_its_name():
    assert guess_type(BUDGET_SHEET, "numbers.xlsx", "xlsx") == "budget"
    assert guess_type("anything", "Budget 2026.xlsx", "xlsx") == "budget"
    assert guess_type("Forecast for next year: revenue 5", "plan.csv", "csv") == "budget"


def test_a_list_of_transactions_is_not_a_budget():
    assert guess_type("date,reference,amount\n2026-03-01,R001,12.50\n2026-03-02,R002,25.00", "lines.csv", "csv") == "other"
    assert guess_type("Revenue | 5", "one.xlsx", "xlsx") == "other"          # one label and no periods


def test_a_statement_of_account_is_known():
    assert guess_type("STATEMENT OF ACCOUNT Opening balance 100", "s.docx", "docx") == "statement"
    assert guess_type("Opening balance 100 ... Closing balance 200", "s.csv", "csv") == "statement"


def test_a_contract_that_mentions_a_budget_is_still_a_contract():
    assert guess_type(CONTRACT + " The budget is fixed.", "c.docx", "docx") == "contract"


# ── what the reader records ──

@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "up"))
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _read(content, name):
    rec = store.put(content, filename=name, inspected=inspect(content, name), user_id=1, session_id="s")
    reader.read_now(rec.id)
    return store.get(rec.id), store.load_extract(rec.id)


def test_the_reader_records_the_guess_for_text_files():
    rec, payload = _read(f.xlsx(), "budget.xlsx")
    assert rec.document_type == "budget" and payload["source"] == "text" and payload["checks"] == []
    rec, _ = _read(f.docx([CONTRACT]), "agreement.docx")
    assert rec.document_type == "contract" and rec.summary == "Contract"
    rec, _ = _read(f.csv_bytes(), "lines.csv")
    assert rec.document_type == "other"


def test_a_text_pdf_that_is_a_contract_gets_the_guess_too():
    rec, _ = _read(f.text_pdf(f.CONTRACT_LINES), "terms.pdf")
    assert rec.document_type == "contract"
