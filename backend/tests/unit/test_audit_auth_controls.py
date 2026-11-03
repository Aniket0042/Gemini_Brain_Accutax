from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from gemini_brain.api import routes
from gemini_brain.api.auth import CurrentUser
from gemini_brain.api.models import LoginRequest


@pytest.mark.parametrize("kind", ["json", "form"])
def test_upstream_refusal_or_mfa_never_falls_back_to_shared_passwords(kind):
    with (patch.object(routes, "authenticate_with_accutax_api", return_value=None),
          patch.object(routes, "get_user_by_email") as local,
          patch.object(routes, "create_access_token") as mint):
        with pytest.raises(HTTPException) as error:
            if kind == "json":
                routes.login_json(LoginRequest(username="audit@example.com", password="password"))
            else:
                routes.login_form(OAuth2PasswordRequestForm(username="audit@example.com", password="password"))
        assert error.value.status_code == 401
        local.assert_not_called()
        mint.assert_not_called()


def test_diagnostics_are_operator_only_and_both_routes_use_the_guard(monkeypatch):
    user = CurrentUser(10, "audit@example.com", [1])
    monkeypatch.setattr(routes.settings, "model_diagnostic_admins", "")
    with pytest.raises(HTTPException) as error:
        routes._diagnostic_operator(user)
    assert error.value.status_code == 403
    monkeypatch.setattr(routes.settings, "model_diagnostic_admins", "audit@example.com")
    assert routes._diagnostic_operator(user) is user
    diagnostic_routes = [r for r in routes.router.routes if r.path.endswith('/health/models')]
    assert len(diagnostic_routes) == 2
    assert all(any(d.call is routes._diagnostic_operator for d in r.dependant.dependencies) for r in diagnostic_routes)
