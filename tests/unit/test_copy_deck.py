"""Unit tests for Phase 5: Complete curated copy deck validation."""
import re
import pytest

from gemini_brain.resilience.errors import ErrorCode
from gemini_brain.resilience.messages import NOTICES, notice_for

LEAK_PATTERNS = [
    r"\bselect\b",
    r"\bpostgres\b",
    r"\bpsycopg2\b",
    r"\btraceback\b",
    r"\bsystem prompt\b",
    r"\bexception\b",
    r"\bnullpointer\b",
    r"\bstack trace\b",
]


def test_all_error_codes_have_curated_copy():
    for code in ErrorCode:
        n = notice_for(code, subject="invoices", request_id="req-12345")
        assert n["code"] == code.value
        assert isinstance(n["title"], str) and len(n["title"]) > 0
        assert isinstance(n["message"], str) and len(n["message"]) > 0
        assert isinstance(n["suggestions"], list)
        assert n["kind"] in ("empty", "partial", "degraded", "denied", "failed")
        assert isinstance(n["retryable"], bool)


def test_no_forbidden_leaks_in_copy_deck():
    for code_str, tpl in NOTICES.items():
        text = f"{tpl.get('title', '')} {tpl.get('message', '')} {' '.join(tpl.get('suggestions', []))}".lower()
        for pattern in LEAK_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            assert match is None, f"Forbidden leak '{pattern}' found in copy for {code_str}: {text}"


def test_notice_for_clean_substitution():
    n = notice_for(
        ErrorCode.NO_ROWS,
        subject="overdue client bills",
        request_id="req-abc-999",
    )
    assert "overdue client bills" in n["message"]
    # Ensure no unresolved template variables like {subject} remain in message
    assert "{" not in n["message"] and "}" not in n["message"]


def test_fallback_for_unknown_code():
    n = notice_for("COMPLETELY_UNKNOWN_CUSTOM_CODE", subject="bills", request_id="req-test")
    assert n["kind"] == "failed"
    assert n["code"] == "COMPLETELY_UNKNOWN_CUSTOM_CODE"
    assert "Something went wrong" in n["message"]
    assert "{" not in n["message"]
