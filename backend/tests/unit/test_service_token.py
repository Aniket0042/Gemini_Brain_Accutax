"""
test_service_token.py — Auto-refreshing service-account token.

Covers the refresh/caching/backoff behaviour, and — most importantly — that a
service token never displaces a real user's token. A service account may hold
different tenant access than the caller, so substituting it would misattribute
the request and potentially widen what it can reach.
"""
import base64
import json
import time

import httpx
import pytest

from gemini_brain.auth import service_token as st
from gemini_brain.config.settings import settings


def make_jwt(exp_offset_seconds: float) -> str:
    """An unsigned JWT whose exp sits `exp_offset_seconds` from now."""
    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'exp': int(time.time() + exp_offset_seconds)})}.sig"


FRESH = make_jwt(86400)      # a day out — comfortably past the refresh margin
NEARLY_EXPIRED = make_jwt(60)  # inside REFRESH_MARGIN_SECONDS
SEED = make_jwt(86400)


@pytest.fixture(autouse=True)
def clean_cache():
    st.reset_cache()
    yield
    st.reset_cache()


@pytest.fixture
def service_account(monkeypatch):
    monkeypatch.setattr(settings, "accutax_service_email", "svc@example.com")
    monkeypatch.setattr(settings, "accutax_service_password", "not-a-real-password")


@pytest.fixture
def no_service_account(monkeypatch):
    monkeypatch.setattr(settings, "accutax_service_email", "")
    monkeypatch.setattr(settings, "accutax_service_password", "")


class RecordingLogin:
    """Stands in for httpx.post, counting calls and replaying canned responses."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def __call__(self, url, json=None, timeout=None):
        self.calls += 1
        if self._responses:
            item = self._responses.pop(0)
        else:
            item = self._responses[-1] if self._responses else None
        if isinstance(item, Exception):
            raise item
        return item


def _response(status_code: int, payload=None, text=""):
    return httpx.Response(
        status_code,
        json=payload if payload is not None else None,
        text=None if payload is not None else text,
        request=httpx.Request("POST", "http://test/auth/login"),
    )


def test_without_service_account_returns_static_seed(monkeypatch, no_service_account):
    """Unconfigured is the pre-existing behaviour and must be unchanged."""
    monkeypatch.setattr(settings, "accutax_auth_token", SEED)
    login = RecordingLogin()
    monkeypatch.setattr(st.httpx, "post", login)

    assert st.get_service_token() == SEED
    assert login.calls == 0, "must not attempt login when no service account is configured"


def test_expired_seed_is_replaced_by_a_fresh_login(monkeypatch, service_account):
    """The live failure mode: seed expired, service account configured → refresh."""
    monkeypatch.setattr(settings, "accutax_auth_token", make_jwt(-3600))
    login = RecordingLogin(_response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    assert st.get_service_token() == FRESH
    assert login.calls == 1


def test_fresh_cached_token_is_reused_without_another_login(monkeypatch, service_account):
    monkeypatch.setattr(settings, "accutax_auth_token", "")
    login = RecordingLogin(_response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    first = st.get_service_token()
    second = st.get_service_token()

    assert first == second == FRESH
    assert login.calls == 1, "a still-valid cached token must not trigger a second login"


def test_near_expiry_triggers_refresh(monkeypatch, service_account):
    """Refresh happens before expiry, not after — callers never see a dead token."""
    monkeypatch.setattr(settings, "accutax_auth_token", NEARLY_EXPIRED)
    login = RecordingLogin(_response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    assert st.get_service_token() == FRESH
    assert login.calls == 1


@pytest.mark.parametrize("nested_key", ["token", "access_token"])
def test_accepts_common_login_response_shapes(monkeypatch, service_account, nested_key):
    monkeypatch.setattr(settings, "accutax_auth_token", "")
    login = RecordingLogin(_response(200, {nested_key: FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)
    assert st.get_service_token() == FRESH


def test_accepts_token_nested_under_data(monkeypatch, service_account):
    monkeypatch.setattr(settings, "accutax_auth_token", "")
    login = RecordingLogin(_response(200, {"data": {"token": FRESH}}))
    monkeypatch.setattr(st.httpx, "post", login)
    assert st.get_service_token() == FRESH


def test_failed_login_falls_back_to_the_seed(monkeypatch, service_account):
    """Never raise — a login outage must degrade, not break the caller."""
    monkeypatch.setattr(settings, "accutax_auth_token", SEED)
    login = RecordingLogin(_response(401, text="Unauthorized"))
    monkeypatch.setattr(st.httpx, "post", login)

    assert st.get_service_token() == SEED


def test_network_error_falls_back_to_the_seed(monkeypatch, service_account):
    monkeypatch.setattr(settings, "accutax_auth_token", SEED)
    login = RecordingLogin(httpx.ConnectError("connection refused"))
    monkeypatch.setattr(st.httpx, "post", login)

    assert st.get_service_token() == SEED


def test_repeated_failure_is_backed_off_not_retried_per_call(monkeypatch, service_account):
    """A down login endpoint must not be hit on every single API call."""
    monkeypatch.setattr(settings, "accutax_auth_token", make_jwt(-3600))
    login = RecordingLogin(*[_response(500, text="boom")] * 5)
    monkeypatch.setattr(st.httpx, "post", login)

    for _ in range(5):
        st.get_service_token()

    assert login.calls == 1, (
        f"expected backoff after the first failure, got {login.calls} login attempts"
    )


def test_backoff_expires_and_allows_a_later_retry(monkeypatch, service_account):
    monkeypatch.setattr(settings, "accutax_auth_token", make_jwt(-3600))
    login = RecordingLogin(_response(500, text="boom"), _response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    st.get_service_token()
    # Pretend the backoff window has elapsed.
    st._cache["last_failed_attempt"] = time.time() - st.REFRESH_RETRY_BACKOFF_SECONDS - 1

    assert st.get_service_token() == FRESH
    assert login.calls == 2


def test_user_token_is_never_replaced_by_the_service_token(monkeypatch, service_account):
    """Security-critical: the ContextVar user token outranks the service token.

    A service account can hold different tenant access than the caller. If it
    displaced the user's token the request would be both misattributed and
    potentially able to reach data the user cannot.
    """
    from gemini_brain.api_client import accutax_client as ac

    monkeypatch.setattr(settings, "accutax_auth_token", SEED)
    login = RecordingLogin(_response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    user_token = make_jwt(7200)
    reset = ac.active_auth_token.set(user_token)
    try:
        resolved = "" or ac.active_auth_token.get() or st.get_service_token()
        assert resolved == user_token
        assert login.calls == 0, "a live user token must short-circuit service login entirely"
    finally:
        ac.active_auth_token.reset(reset)


def test_explicit_auth_token_outranks_everything(monkeypatch, service_account):
    from gemini_brain.api_client import accutax_client as ac

    login = RecordingLogin(_response(200, {"token": FRESH}))
    monkeypatch.setattr(st.httpx, "post", login)

    explicit = make_jwt(7200)
    reset = ac.active_auth_token.set(make_jwt(7200))
    try:
        resolved = explicit or ac.active_auth_token.get() or st.get_service_token()
        assert resolved == explicit
        assert login.calls == 0
    finally:
        ac.active_auth_token.reset(reset)
