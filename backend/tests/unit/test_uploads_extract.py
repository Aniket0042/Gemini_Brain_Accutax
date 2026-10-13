"""DocumentExtract: tolerant of what a model returns, strict about what it keeps."""
from gemini_brain.uploads.extract import (
    BILL_TYPES, MAX_ITEMS, RECORD_TOOL, DocumentExtract, normalize_currency)


def test_numbers_are_read_from_how_models_write_them():
    ex = DocumentExtract.from_dict({"subtotal": "1,234.50", "tax_total": "AED 61.73", "grand_total": 1296.23,
                                    "tax_rate_percent": "5%", "opening_balance": "12.345,67", "closing_balance": True})
    assert (ex.subtotal, ex.tax_total, ex.grand_total, ex.tax_rate_percent) == (1234.5, 61.73, 1296.23, 5.0)
    assert ex.opening_balance == 12345.67   # European grouping
    assert ex.closing_balance is None       # a boolean is not a number


def test_a_decimal_comma_is_a_decimal_comma():
    assert DocumentExtract.from_dict({"subtotal": "14,10"}).subtotal == 14.10
    assert DocumentExtract.from_dict({"subtotal": "1,000"}).subtotal == 1000.0


def test_dates_are_kept_only_when_they_are_dates():
    assert DocumentExtract.from_dict({"date": "2018-07-21"}).date == "2018-07-21"
    assert DocumentExtract.from_dict({"date": "21/07/2018"}).date is None   # the model was told to convert
    assert DocumentExtract.from_dict({"date": "2018-13-45"}).date is None


def test_currency_marks_become_iso_codes():
    assert normalize_currency("RM") == "MYR" and normalize_currency("dhs") == "AED" and normalize_currency("€") == "EUR"
    assert normalize_currency("AED") == "AED" and normalize_currency("dirhams") is None and normalize_currency("") is None


def test_unknown_document_type_becomes_other():
    assert DocumentExtract.from_dict({"document_type": "invoice-ish"}).document_type == "other"
    assert DocumentExtract.from_dict({"document_type": "tax_invoice"}).is_bill


def test_items_and_statement_lines_are_cleaned_and_capped():
    ex = DocumentExtract.from_dict({
        "items": [{"description": "  Transistor \n x2 ", "quantity": "2", "rate": 75, "amount": "150"}, "junk", None]
                 + [{"amount": 1}] * MAX_ITEMS,
        "statement_lines": [{"date": "2026-01-02", "reference": "INV-1", "debit": "100.50"}, 5],
    })
    assert len(ex.items) == MAX_ITEMS and ex.items[0].description == "Transistor x2" and ex.items[0].amount == 150.0
    assert ex.statement_lines[0].debit == 100.5 and len(ex.statement_lines) == 1


def test_a_round_trip_keeps_everything():
    ex = DocumentExtract.from_dict({"document_type": "tax_invoice", "vendor_name": "ACME LLC", "vendor_tax_id": "100123456700003",
                                    "document_number": "A-1", "date": "2026-05-01", "currency": "AED", "grand_total": 105,
                                    "items": [{"description": "x", "amount": 105}], "text": "hello", "unreadable_fields": ["buyer_tax_id"]})
    assert DocumentExtract.from_dict(ex.to_dict()) == ex


def test_garbage_input_gives_an_empty_extract_not_an_error():
    for raw in (None, {}, {"items": "no", "statement_lines": 3, "unreadable_fields": None}):
        assert DocumentExtract.from_dict(raw).document_type == "other"


def test_summary_is_one_line_for_the_chip():
    ex = DocumentExtract.from_dict({"document_type": "tax_invoice", "vendor_name": "ACME LLC", "currency": "AED", "grand_total": 525})
    assert ex.summary() == "Tax invoice · ACME LLC · AED 525.00"
    assert DocumentExtract.from_dict({"text": "x"}).summary() == "Document"


def test_the_tool_schema_requires_only_fields_it_defines():
    schema = RECORD_TOOL["toolSpec"]["inputSchema"]["json"]
    assert set(schema["required"]) <= set(schema["properties"])
    assert set(BILL_TYPES) <= set(schema["properties"]["document_type"]["enum"])
    # every field of the dataclass the model can fill is in the schema
    assert {"vendor_name", "buyer_name", "statement_lines", "text"} <= set(schema["properties"])
