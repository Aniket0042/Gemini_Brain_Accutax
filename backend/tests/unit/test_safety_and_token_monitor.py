"""
test_safety_and_token_monitor.py — Unit tests for the JWT token monitor.
"""
import base64
import json
import time

from gemini_brain.auth.token_monitor import inspect_jwt_token, TokenHealth


def _make_mock_jwt(claims: dict) -> str:
    """Create a mock unsigned 3-part JWT for testing."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode("utf-8").rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("utf-8").rstrip("=")
    sig = "mock_signature_for_testing"
    return f"{header}.{payload}.{sig}"


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
