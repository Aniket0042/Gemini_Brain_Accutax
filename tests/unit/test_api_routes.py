"""Unit tests for FastAPI REST API endpoints & Swagger documentation."""
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from gemini_brain.api.app import app

client = TestClient(app)


def test_health_check_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "gemini-brain-api"


def test_swagger_ui_endpoint():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Swagger UI" in response.text or "html" in response.text.lower()


def test_openapi_schema_endpoint():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Gemini Brain AI Orchestration Engine"
    assert "/api/v1/query" in schema["paths"]


@patch("gemini_brain.api.routes.GeminiBrainRunner")
def test_query_endpoint_success(mock_runner_cls):
    mock_runner = MagicMock()
    mock_runner_cls.return_value = mock_runner
    mock_runner.run.return_value = {
        "answer": "Test answer",
        "sql": None,
        "results": [],
        "error": None,
        "token_usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "llm_calls": 2,
            "cost_usd": 0.001,
            "elapsed_seconds": 0.5,
        },
        "agent_trace": [],
        "routing_info": {
            "type": 1,
            "type_label": "FAQ/How-to",
            "path": "gemini_direct",
            "reason": "faq",
        },
    }

    from gemini_brain.api.auth import create_access_token
    token = create_access_token(user_id=18, email="test@example.com", allowed_org_ids=[27])

    response = client.post(
        "/api/v1/query",
        json={"query": "How do I create invoice?", "organization_id": 27},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Test answer"
    assert data["routing_info"]["type"] == 1
