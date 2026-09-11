"""Unit tests for configuration, settings, and constants."""
from gemini_brain.config.constants import (
    COMPLEXITY_MODEL_MAP,
    GEMINI_MODEL,
    LEFT_PATH_TYPES,
    RIGHT_PATH_TYPES,
)
from gemini_brain.config.pricing import gemini_brain_cost
from gemini_brain.config.settings import settings


def test_settings_defaults():
    assert settings.bedrock_region == "ap-south-1"
    assert isinstance(settings.cors_origins, list)


def test_constants():
    assert GEMINI_MODEL in ("gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash")
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
