"""
test_safety_and_token_monitor.py — Unit tests for Phase F SQL read-only safety checks & JWT token monitor.
"""
import base64
import json
import time
import pytest

from gemini_brain.sql_fallback.sql_safety import assert_read_only
from gemini_brain.auth.token_monitor import inspect_jwt_token, TokenHealth


def _make_mock_jwt(claims: dict) -> str:
    """Create a mock unsigned 3-part JWT for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode("utf-8").rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
    sig = "mock_signature_for_testing"
    return f"{header}.{payload}.{sig}"


# ── 1. SQL Safety (assert_read_only) Tests ────────────────────────────────────

def test_assert_read_only_allowed_queries():
    """Verify that read-only SELECT queries pass without exception."""
    assert_read_only("SELECT id, name, total FROM income WHERE organization_id = 27")
    assert_read_only("SELECT c.name, SUM(i.total) FROM contacts c JOIN income i ON c.id = i.contact_id GROUP BY c.name")
    assert_read_only("SELECT * FROM chart_of_accounts ORDER BY account_code ASC")


def test_assert_read_only_string_literal_tolerance():
    """Verify that forbidden keywords inside single-quoted string literals do not raise false positives."""
    assert_read_only("SELECT * FROM audit_logs WHERE message LIKE '%updated invoice status%'")
    assert_read_only("SELECT * FROM items WHERE description = 'please delete later'")
    assert_read_only("SELECT * FROM contacts WHERE notes = 'drop shipment customer'")


def test_assert_read_only_catches_forbidden_keywords():
    """Verify that mutating operations are strictly rejected."""
    with pytest.raises(ValueError, match="Forbidden SQL operation detected: insert"):
        assert_read_only("INSERT INTO contacts (name) VALUES ('Hacker')")

    with pytest.raises(ValueError, match="Forbidden SQL operation detected: update"):
        assert_read_only("UPDATE income SET total = 0 WHERE organization_id = 27")

    with pytest.raises(ValueError, match="Forbidden SQL operation detected: delete"):
        assert_read_only("DELETE FROM expense WHERE id = 10")

    with pytest.raises(ValueError, match="Forbidden SQL operation detected: drop"):
        assert_read_only("DROP TABLE organizations CASCADE")

    with pytest.raises(ValueError, match="Forbidden SQL operation detected: alter"):
        assert_read_only("ALTER TABLE users ADD COLUMN is_admin boolean")

    with pytest.raises(ValueError, match="Forbidden SQL operation detected: truncate"):
        assert_read_only("TRUNCATE TABLE bank_accounts")


# ── 2. JWT Token Expiration Monitor Tests ────────────────────────────────────

def test_inspect_jwt_missing():
    """Verify missing/empty token returns MISSING status."""
    res = inspect_jwt_token("")
    assert res.status == "MISSING"
    assert res.valid is False


def test_inspect_jwt_malformed():
    """Verify malformed non-JWT string returns INVALID status."""
    res = inspect_jwt_token("not-a-jwt-token")
    assert res.status == "INVALID"
    assert res.valid is False


def test_inspect_jwt_healthy():
    """Verify token expiring in 7 days returns HEALTHY status."""
    future_exp = time.time() + (7 * 86400)
    tok = _make_mock_jwt({"sub": "123", "org": 27, "exp": future_exp})
    res = inspect_jwt_token(tok)
    assert res.status == "HEALTHY"
    assert res.valid is True
    assert res.seconds_remaining is not None
    assert res.seconds_remaining > 86400


def test_inspect_jwt_expiring_soon():
    """Verify token expiring in 4 hours returns EXPIRING_SOON warning."""
    soon_exp = time.time() + (4 * 3600)
    tok = _make_mock_jwt({"sub": "123", "org": 27, "exp": soon_exp})
    res = inspect_jwt_token(tok)
    assert res.status == "EXPIRING_SOON"
    assert res.valid is True
    assert res.seconds_remaining is not None
    assert res.seconds_remaining < 86400


def test_inspect_jwt_expired():
    """Verify expired token returns EXPIRED status."""
    past_exp = time.time() - (2 * 3600)
    tok = _make_mock_jwt({"sub": "123", "org": 27, "exp": past_exp})
    res = inspect_jwt_token(tok)
    assert res.status == "EXPIRED"
    assert res.valid is False
    assert res.seconds_remaining is not None
    assert res.seconds_remaining < 0
