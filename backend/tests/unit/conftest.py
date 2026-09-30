"""Unit-test isolation for report generation and live organization lookups."""
import pytest


@pytest.fixture(autouse=True)
def _no_live_org_lookup(monkeypatch):
    """Unit tests never ask Accutax or the database which orgs a user may query.

    Both lookups report "unavailable", so get_current_user falls back to the
    org list signed into the test token. Tests of the live path patch these
    functions themselves.
    """
    from gemini_brain.api import auth

    monkeypatch.setattr(auth, "_fetch_accutax_orgs", lambda token, user_id: None)
    monkeypatch.setattr(auth, "_query_user_allowed_orgs", lambda user_id, db_name="": None)
    auth._DB_ORG_CACHE.clear()
    auth._ACCUTAX_ORG_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_multi_org_plan_log_file(monkeypatch):
    """Unit tests never append to the real multi-org plan log; tests of it set their own path."""
    from gemini_brain.config.settings import settings

    monkeypatch.setattr(settings, "multi_org_plan_log", "")


@pytest.fixture(autouse=True)
def _no_report_narration_model(monkeypatch):
    """Unit tests never call Bedrock for report narration.

    The narrator falls back to its template when the model call fails, which
    is exactly the offline behaviour; tests that exercise the model path
    replace `narrator._model_call` with their own fake.
    """
    from gemini_brain.artifacts import narrator

    def _offline(system: str, user: str) -> str:
        raise RuntimeError("report narration model is not available in unit tests")

    monkeypatch.setattr(narrator, "_model_call", _offline)
