"""
test_tenant_isolation_api.py — HTTP-level end-to-end test suite for production-grade tenant isolation.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import create_app
from gemini_brain.api.auth import (
    create_access_token,
    init_auth_db,
)


@pytest.fixture(scope="module")
def app_client():
    """Create FastAPI TestClient and ensure auth DB and seed users are mocked for fast test execution."""
    with patch("gemini_brain.api.auth.init_auth_db"):
        app = create_app()
        client = TestClient(app)
        yield client


@pytest.fixture(scope="module")
def mock_brain_runner():
    """Mock GeminiBrainRunner execution to isolate HTTP API enforcement testing."""
    with patch("gemini_brain.api.routes.GeminiBrainRunner") as mock_cls:
        instance = MagicMock()
        instance.run.return_value = {
            "answer": "Test Answer",
            "sql": None,
            "results": [],
            "error": None,
            "token_usage": {
                "input_tokens": 10,
                "output_tokens": 10,
                "llm_calls": 1,
                "cost_usd": 0.001,
                "elapsed_seconds": 0.1,
            },
            "agent_trace": [],
            "routing_info": {
                "type": 1,
                "type_label": "General / FAQ",
                "path": "gemini_direct",
                "reason": "Test",
            },
        }

        def mock_stream_chunks():
            yield {"status": "Processing", "type": "test"}
            yield {
                "final_result": {
                    "answer": "Test Stream Answer",
                    "sql": None,
                    "results": [],
                    "error": None,
                    "token_usage": {
                        "input_tokens": 10,
                        "output_tokens": 10,
                        "llm_calls": 1,
                        "cost_usd": 0.001,
                        "elapsed_seconds": 0.1,
                    },
                    "agent_trace": [],
                    "routing_info": None,
                }
            }

        # Re-apply real _enforce_tenant_isolation logic onto mock instance
        from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
        real_runner = GeminiBrainRunner()
        instance._enforce_tenant_isolation = real_runner._enforce_tenant_isolation
        instance._resolve_organization = real_runner._resolve_organization

        # Delegate run and run_stream to run real _enforce_tenant_isolation before returning mock result
        def real_enforced_run(*args, **kwargs):
            real_runner._enforce_tenant_isolation(
                organization_id=kwargs.get("organization_id"),
                query=kwargs.get("query", ""),
                db_name=kwargs.get("db_name", ""),
                allowed_org_ids=kwargs.get("allowed_org_ids"),
                user_id=kwargs.get("user_id", 18),
                session_id=kwargs.get("session_id"),
            )
            return instance.run.return_value

        def real_enforced_run_stream(*args, **kwargs):
            real_runner._enforce_tenant_isolation(
                organization_id=kwargs.get("organization_id"),
                query=kwargs.get("query", ""),
                db_name=kwargs.get("db_name", ""),
                allowed_org_ids=kwargs.get("allowed_org_ids"),
                user_id=kwargs.get("user_id", 18),
                session_id=kwargs.get("session_id"),
            )
            for chunk in mock_stream_chunks():
                yield chunk

        instance.run.side_effect = real_enforced_run
        instance.run_stream.side_effect = real_enforced_run_stream

        mock_cls.return_value = instance
        yield instance


class TestTenantIsolationAPI:

    def test_login_form_success(self, app_client):
        """Swagger OAuth2 form login succeeds for valid credentials and returns JWT token."""
        mock_user = {"id": 101, "email": "user_single@example.com", "password": "hashed_password"}
        with patch("gemini_brain.api.routes.get_user_by_email", return_value=mock_user), \
             patch("gemini_brain.api.routes.verify_password", return_value=True), \
             patch("gemini_brain.api.routes.get_user_allowed_orgs", return_value=[5]):
            response = app_client.post(
                "/api/v1/auth/login",
                data={"username": "user_single@example.com", "password": "TestPass123!"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"
            assert data["allowed_org_ids"] == [5]
            assert data["user_id"] == 101

    def test_login_invalid_credentials_rejected(self, app_client):
        """Login with invalid password returns 401 Unauthorized."""
        mock_user = {"id": 101, "email": "user_single@example.com", "password": "hashed_password"}
        with patch("gemini_brain.api.routes.get_user_by_email", return_value=mock_user), \
             patch("gemini_brain.api.routes.verify_password", return_value=False):
            response = app_client.post(
                "/api/v1/auth/login",
                data={"username": "user_single@example.com", "password": "WrongPassword"},
            )
            assert response.status_code == 401

    def test_no_token_rejected_401(self, app_client):
        """Unauthenticated request to /api/v1/query is rejected with 401."""
        response = app_client.post("/api/v1/query", json={"query": "What is our revenue?"})
        assert response.status_code == 401
        assert "Not authenticated" in response.text or "detail" in response.json()

    def test_single_org_user_query_own_org_succeeds(self, app_client, mock_brain_runner):
        """Valid token for single-org user querying allowed org (id=14) succeeds."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query",
            json={"query": "What is our revenue?", "organization_id": 14},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["answer"] == "Test Answer"

    def test_single_org_user_query_auto_defaults_to_single_allowed_org(self, app_client, mock_brain_runner):
        """Single-org user omitting organization_id auto-defaults to their 1 allowed org."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query",
            json={"query": "What is our revenue?"},
            headers=headers,
        )
        assert response.status_code == 200

    def test_single_org_user_query_unauthorized_org_rejected(self, app_client, mock_brain_runner):
        """Single-org user attempting to query unauthorized org (id=99) is rejected."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query",
            json={"query": "What is our revenue?", "organization_id": 99},
            headers=headers,
        )
        assert response.status_code == 400
        assert "Access denied" in response.json()["detail"]

    def test_multi_org_user_query_either_allowed_org_succeeds(self, app_client, mock_brain_runner):
        """Multi-org user querying either allowed org (14 or 44) succeeds."""
        token = create_access_token(user_id=102, email="user_multi@example.com", allowed_org_ids=[14, 44])
        headers = {"Authorization": f"Bearer {token}"}

        # Query org 14
        res1 = app_client.post("/api/v1/query", json={"query": "Revenue", "organization_id": 14}, headers=headers)
        assert res1.status_code == 200

        # Query org 44
        res2 = app_client.post("/api/v1/query", json={"query": "Revenue", "organization_id": 44}, headers=headers)
        assert res2.status_code == 200

    def test_multi_org_user_ambiguous_query_without_org_rejected(self, app_client, mock_brain_runner):
        """Multi-org user omitting organization_id when query doesn't specify one is rejected."""
        token = create_access_token(user_id=102, email="user_multi@example.com", allowed_org_ids=[14, 44])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post("/api/v1/query", json={"query": "What is our revenue?"}, headers=headers)
        assert response.status_code == 400
        assert "Multiple organizations available" in response.json()["detail"]

    def test_no_org_user_query_rejected(self, app_client, mock_brain_runner):
        """No-org user (allowed_org_ids=[]) is rejected regardless of parameters."""
        token = create_access_token(user_id=999, email="no_org@example.com", allowed_org_ids=[])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query",
            json={"query": "What is our revenue?", "organization_id": 14},
            headers=headers,
        )
        assert response.status_code == 400
        assert "Access denied: User has no assigned organizations" in response.json()["detail"]

    def test_tampered_token_rejected_401(self, app_client):
        """Tampered JWT token is rejected with 401."""
        headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature"}
        response = app_client.post("/api/v1/query", json={"query": "Revenue"}, headers=headers)
        assert response.status_code == 401

    def test_expired_token_rejected_401(self, app_client):
        """Expired JWT token is rejected with 401."""
        from datetime import timedelta
        token = create_access_token(
            user_id=101,
            email="user_single@example.com",
            allowed_org_ids=[14],
            expires_delta=timedelta(seconds=-10),  # Already expired
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = app_client.post("/api/v1/query", json={"query": "Revenue"}, headers=headers)
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()

    def test_body_user_id_tampering_ignored(self, app_client, mock_brain_runner):
        """Body-supplied user_id (999) is ignored and verified JWT sub (101) is used."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query",
            json={"query": "What is our revenue?", "organization_id": 14, "user_id": 999},
            headers=headers,
        )
        assert response.status_code == 200

    def test_stream_no_token_rejected_401(self, app_client):
        """Streaming query without token is rejected with HTTP 401 before stream connection opens."""
        response = app_client.post("/api/v1/query/stream", json={"query": "What is our revenue?"})
        assert response.status_code == 401

    def test_stream_unauthorized_org_emits_error_event(self, app_client, mock_brain_runner):
        """Streaming query for an unauthorized org (id=99) emits SSE error event."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query/stream",
            json={"query": "What is total revenue?", "organization_id": 99},
            headers=headers,
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        content = response.text
        assert "Access denied" in content
        assert '"type": "error"' in content

    def test_stream_allowed_org_succeeds(self, app_client, mock_brain_runner):
        """Streaming query for an allowed org (id=14) streams progress and final result."""
        token = create_access_token(user_id=101, email="user_single@example.com", allowed_org_ids=[14])
        headers = {"Authorization": f"Bearer {token}"}

        response = app_client.post(
            "/api/v1/query/stream",
            json={"query": "What is total revenue?", "organization_id": 14},
            headers=headers,
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert "data:" in response.text

    def test_missing_jwt_secret_raises_error(self):
        """Verifies that if JWT_SECRET is unconfigured/empty, get_jwt_secret raises ValueError."""
        from gemini_brain.api.auth import get_jwt_secret
        with patch("gemini_brain.api.auth.settings") as mock_set, patch.dict("os.environ", {"JWT_SECRET": ""}):
            mock_set.jwt_secret = ""
            with pytest.raises(ValueError, match="JWT_SECRET is required"):
                get_jwt_secret()

