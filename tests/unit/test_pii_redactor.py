"""
test_pii_redactor.py — Unit tests for Presidio-based PII Redactor.
"""
import pytest
from gemini_brain.pii.redactor import redact_pii


def test_redact_email():
    text = "Please contact me at john.doe@acme.com for the invoice."
    redacted, counts = redact_pii(text)
    assert "[EMAIL_REDACTED]" in redacted
    assert "john.doe@acme.com" not in redacted
    assert counts["EMAIL_ADDRESS"] == 1


def test_redact_phone_uae():
    text = "Call me at +971 50 123 4567 or local 050 987 6543."
    redacted, counts = redact_pii(text)
    assert "[PHONE_REDACTED]" in redacted
    assert "+971 50 123 4567" not in redacted
    assert "050 987 6543" not in redacted
    assert counts["PHONE_NUMBER"] >= 2


def test_redact_credit_card():
    # Luhn valid test credit card number (4532... Visa test number)
    text = "Charge my card 4532015112830366 for AED 500."
    redacted, counts = redact_pii(text)
    assert "[CARD_REDACTED]" in redacted
    assert "4532015112830366" not in redacted
    assert counts["CREDIT_CARD"] == 1


def test_redact_iban():
    text = "Transfer money to IBAN AE070330000000000001234."
    redacted, counts = redact_pii(text)
    assert "[IBAN_REDACTED]" in redacted
    assert "AE070330000000000001234" not in redacted
    assert counts["IBAN_CODE"] == 1


def test_redact_emirates_id():
    text = "My Emirates ID is 784-1990-1234567-1 please verify."
    redacted, counts = redact_pii(text)
    assert "[ID_REDACTED]" in redacted
    assert "784-1990-1234567-1" not in redacted
    assert counts["UAE_EMIRATES_ID"] == 1


def test_negative_financial_queries():
    queries = [
        "show total revenue for organization Zero-Config for 2026",
        "show invoice INV-2026-001 amount AED 50,000.00",
        "account 1020 balance on 2026-08-05",
        "what is the profit and loss for quarter Q1 2026",
        "list top 5 customers for org 69",
    ]
    for q in queries:
        redacted, counts = redact_pii(q)
        assert redacted == q, f"Query incorrectly modified: '{q}' -> '{redacted}'"
        assert sum(counts.values()) == 0


def test_negative_long_numeric_references():
    """Verify long numeric invoice/account/reference numbers (13-19 digits) are not redacted as credit cards."""
    numeric_refs = [
        "show invoice 123456789012345",         # 15 digits
        "details for reference 9876543210987654", # 16 digits (non-Luhn)
        "account number 10002000300040005",      # 17 digits
        "transaction REF883920194857362910",    # 18 digits with prefix
        "query invoice 20260805123456789",       # 17 digits
    ]
    for q in numeric_refs:
        redacted, counts = redact_pii(q)
        assert redacted == q, f"Long numeric reference incorrectly redacted: '{q}' -> '{redacted}'"
        assert counts.get("CREDIT_CARD", 0) == 0

