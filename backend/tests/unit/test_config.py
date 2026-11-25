"""Unit tests for configuration, settings, and constants."""
from gemini_brain.config.constants import (
    COMPLEXITY_MODEL_MAP,
    LEFT_PATH_TYPES,
    RIGHT_PATH_TYPES,
)
from gemini_brain.config.pricing import gemini_brain_cost
from gemini_brain.config.settings import settings


def test_settings_defaults():
    assert settings.bedrock_region == "ap-south-1"
    assert isinstance(settings.cors_origins, list)


def test_constants():
    assert 1 in LEFT_PATH_TYPES
    assert 4 in RIGHT_PATH_TYPES
    assert "SIMPLE" in COMPLEXITY_MODEL_MAP


def test_cost_calculation():
    # 1M Gemini in ($0.15) + 1M Gemini out ($0.60) = $0.75
    cost = gemini_brain_cost(
        gemini_input=1_000_000,
        gemini_output=1_000_000,
        bedrock_input=0,
        bedrock_output=0,
        bedrock_model_id="gemini",
    )
    assert cost == 0.75


def test_dev_cors_defaults_include_ai_frontend_port():
    """The AI frontend runs on 5175 (vite strictPort); 5174 is no longer used."""
    from gemini_brain.config.settings import GeminiBrainSettings

    default = GeminiBrainSettings.model_fields["cors_origins"].default
    assert "http://localhost:5175" in default
    assert "http://127.0.0.1:5175" in default
    assert "http://localhost:5174" not in default


def test_legacy_mode_allows_ai_frontend_origin_and_gateway_mode_does_not(monkeypatch):
    from fastapi.testclient import TestClient

    from gemini_brain.api.app import create_app

    headers = {"Origin": "http://127.0.0.1:5175", "Access-Control-Request-Method": "GET"}
    monkeypatch.setattr(settings, "cors_origins", [])
    monkeypatch.setattr(settings, "ai_auth_mode", "legacy")
    legacy = TestClient(create_app()).options("/api/v1/health", headers=headers)
    assert legacy.headers.get("access-control-allow-origin") == "http://127.0.0.1:5175"

    monkeypatch.setattr(settings, "ai_auth_mode", "gateway")
    gateway = TestClient(create_app()).options("/api/v1/health", headers=headers)
    assert "access-control-allow-origin" not in gateway.headers
