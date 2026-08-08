"""Unit tests for Model Diagnostics and Health Check endpoint."""
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.health.model_health_checker import (
    check_all_models_and_services,
    check_gemini_model,
)

client = TestClient(app)


@patch("gemini_brain.health.model_health_checker.check_gemini_model")
@patch("gemini_brain.health.model_health_checker.check_bedrock_model")
@patch("gemini_brain.health.model_health_checker.check_accutax_api")
@patch("gemini_brain.health.model_health_checker.check_postgres_db")
def test_model_health_checker_aggregated(mock_db, mock_api, mock_bedrock, mock_gemini):
    mock_gemini.return_value = {
        "name": "Google Gemini 2.5 Flash",
        "model_id": "gemini-2.5-flash",
        "provider": "Google GenAI",
        "status": "ok",
        "latency_ms": 150,
        "sample_response": "OK",
        "error": None,
    }
    mock_bedrock.return_value = {
        "name": "AWS Bedrock Claude 3.5 Sonnet",
        "model_id": "apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
        "provider": "AWS Bedrock",
        "status": "ok",
        "latency_ms": 250,
        "sample_response": "OK",
        "error": None,
    }
    mock_api.return_value = {
        "service": "Accutax REST API",
        "target": "http://13.127.157.108:8081/health",
        "status": "ok",
        "http_code": 200,
        "latency_ms": 50,
        "error": None,
    }
    mock_db.return_value = {
        "service": "PostgreSQL Database Engine",
        "target": "127.0.0.1:5435/accutax_bk_1_5",
        "status": "ok",
        "latency_ms": 10,
        "error": None,
    }

    res = check_all_models_and_services()
    assert res["overall_status"] == "ok"
    assert res["summary"]["models_healthy"] == 3
    assert res["summary"]["services_healthy"] == 2


@patch("gemini_brain.api.routes.check_all_models_and_services")
def test_get_health_models_endpoint(mock_check):
    mock_check.return_value = {
        "overall_status": "ok",
        "summary": {
            "models_tested": 3,
            "models_healthy": 3,
            "services_tested": 2,
            "services_healthy": 2,
        },
        "models": [
            {
                "name": "Google Gemini 2.5 Flash",
                "model_id": "gemini-2.5-flash",
                "provider": "Google GenAI",
                "status": "ok",
                "latency_ms": 120,
                "sample_response": "OK",
                "error": None,
            }
        ],
        "services": [
            {
                "service": "Accutax REST API",
                "target": "http://13.127.157.108:8081/health",
                "status": "ok",
                "http_code": 200,
                "latency_ms": 45,
                "error": None,
            }
        ],
    }

    response = client.get("/api/v1/health/models")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "ok"
    assert len(data["models"]) == 1
