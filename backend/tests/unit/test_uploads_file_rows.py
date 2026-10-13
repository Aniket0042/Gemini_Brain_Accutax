"""Charts and exports of the attached files' own figures (uploads.blocks.file_rows)."""
from types import SimpleNamespace

from gemini_brain.uploads.blocks import file_rows
from gemini_brain.uploads.extract import DocumentExtract, ExtractItem, StatementLine


def doc(name, **fields):
    ex = DocumentExtract(**fields)
    return SimpleNamespace(rec=SimpleNamespace(filename=name), extract=ex)


def bill(name, number, total):
    return doc(name, document_type="tax_invoice", vendor_name="Acme", document_number=number, date="2026-01-05", currency="AED",
               subtotal=total / 1.05, tax_total=total - total / 1.05, grand_total=total,
               items=[ExtractItem("Goods", 1, total / 1.05, total / 1.05)])


def test_two_bills_give_one_row_each():
    rows = file_rows([bill("a.pdf", "A-1", 105.0), bill("b.pdf", "B-2", 210.0)])
    assert [r["number"] for r in rows] == ["A-1", "B-2"]
    assert rows[0]["file"] == "a.pdf" and rows[1]["total"] == 210.0


def test_one_bill_gives_its_lines():
    rows = file_rows([bill("a.pdf", "A-1", 105.0)])
    assert rows == [{"description": "Goods", "quantity": 1, "rate": 100.0, "amount": 100.0}]


def test_a_statement_gives_its_lines():
    s = doc("s.pdf", document_type="statement", statement_lines=[StatementLine("2026-01-01", "R1", "Goods", 5.0, None, 5.0)])
    assert file_rows([s])[0]["reference"] == "R1"


def test_other_files_give_no_rows():
    assert file_rows([doc("c.pdf", document_type="contract", text="words")]) == []
    assert file_rows([]) == []
