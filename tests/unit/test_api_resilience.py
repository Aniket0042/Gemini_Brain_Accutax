"""Unit tests for Phase 4: FastAPI & SSE layer resilience and correlation IDs."""
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest

from gemini_brain.api.app import app
from gemini_brain.resilience.errors import AppError, ErrorCode


@pytest.fixture
def client():
    # Mock init_auth_db so test doesn't attempt to connect to external Postgres
    with patch("gemini_brain.api.auth.init_auth_db"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def test_request_correlation_header_generated(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


def test_custom_request_correlation_header_preserved(client):
    custom_id = "custom-test-req-12345"
    resp = client.get("/api/v1/health", headers={"X-Request-ID": custom_id})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == custom_id


def test_validation_error_returns_user_safe_envelope(client):
    # Missing required 'query' and 'organization_id'
    resp = client.post("/api/v1/query", json={})
    assert resp.status_code == 422
    data = resp.json()
    assert "notice" in data
    assert data["notice"]["code"] == "VALIDATION_FAILED"
    assert "results" in data
    assert data["results"] == []
    assert "request_id" in data


@patch("gemini_brain.api.routes.get_current_user")
@patch("gemini_brain.api.routes.GeminiBrainRunner")
def test_query_success_returns_normalized_envelope(mock_runner_cls, mock_auth, client):
    mock_auth.return_value = MagicMock(user_id=1, allowed_org_ids=[1])
    mock_runner = MagicMock()
    mock_runner.run.return_value = {
        "answer": "Total revenue is AED 100,000.00",
        "sql": None,
        "results": [{"total": 100000}],
        "error": None,
        "token_usage": {"input_tokens": 50, "output_tokens": 20, "llm_calls": 1},
        "agent_trace": [],
        "routing_info": None,
    }
    mock_runner_cls.return_value = mock_runner

    resp = client.post(
        "/api/v1/query",
        json={"query": "total revenue", "organization_id": 1},
        headers={"Authorization": "Bearer mock-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["answer"] == "Total revenue is AED 100,000.00"
    assert data["results"] == [{"total": 100000}]
    assert "request_id" in data


@patch("gemini_brain.api.routes.get_current_user")
@patch("gemini_brain.api.routes.GeminiBrainRunner")
def test_query_stream_catches_fatal_error_and_emits_notice(mock_runner_cls, mock_auth, client):
    mock_auth.return_value = MagicMock(user_id=1, allowed_org_ids=[1])
    mock_runner = MagicMock()
    mock_runner.run_stream.side_effect = TimeoutError("Connection to LLM timed out")
    mock_runner_cls.return_value = mock_runner

    resp = client.post(
        "/api/v1/query/stream",
        json={"query": "total revenue", "organization_id": 1},
        headers={"Authorization": "Bearer mock-token"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: error" or '"type": "error"' in body
    assert "UPSTREAM_TIMEOUT" in body
    assert "final_result" in body
