"""Unit tests for the health check of the agent's model(s), Cube and PostgreSQL."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.health import model_health_checker as health
from gemini_brain.semantic.cube_client import CubeError

client = TestClient(app)

MODEL_OK = {"name": "AWS Bedrock Claude Sonnet 5", "model_id": "in.anthropic.claude-sonnet-5",
            "provider": "AWS Bedrock", "status": "ok", "latency_ms": 250, "sample_response": "OK", "error": None}
CUBE_OK = {"service": "Cube semantic layer", "target": "http://127.0.0.1:4000/cubejs-api/v1/meta",
           "status": "ok", "http_code": 200, "latency_ms": 40, "error": None}
DB_OK = {"service": "PostgreSQL Database Engine", "target": "127.0.0.1:5432/accutax", "status": "ok",
         "latency_ms": 10, "error": None}


def test_the_agent_models_are_the_planner_and_a_different_writer(monkeypatch):
    monkeypatch.setattr(health.settings, "agent_model_id", "planner-model")
    monkeypatch.setattr(health.settings, "agent_answer_model_id", "")
    assert health.agent_model_ids() == ["planner-model"]
    monkeypatch.setattr(health.settings, "agent_answer_model_id", "writer-model")
    assert health.agent_model_ids() == ["planner-model", "writer-model"]
    monkeypatch.setattr(health.settings, "agent_answer_model_id", "planner-model")
    assert health.agent_model_ids() == ["planner-model"]


@patch.object(health, "check_postgres_db", return_value=DB_OK)
@patch.object(health, "check_cube", return_value=CUBE_OK)
@patch.object(health, "check_bedrock_model", return_value=MODEL_OK)
def test_all_ok_checks_the_agent_model_cube_and_postgres(mock_bedrock, mock_cube, mock_db, monkeypatch):
    monkeypatch.setattr(health.settings, "agent_model_id", "planner-model")
    monkeypatch.setattr(health.settings, "agent_answer_model_id", "")
    res = health.check_all_models_and_services()
    assert res["overall_status"] == "ok"
    assert res["summary"] == {"models_tested": 1, "models_healthy": 1, "services_tested": 2, "services_healthy": 2}
    assert mock_bedrock.call_args.args[0] == "planner-model"
    assert [s["service"] for s in res["services"]] == ["Cube semantic layer", "PostgreSQL Database Engine"]


@patch.object(health, "check_postgres_db", return_value=DB_OK)
@patch.object(health, "check_bedrock_model", return_value=MODEL_OK)
def test_cube_down_is_degraded(mock_bedrock, mock_db, monkeypatch):
    def unreachable(**kwargs):
        raise CubeError("Cube unreachable: connection refused")

    monkeypatch.setattr(health.cube_client, "meta", unreachable)
    res = health.check_all_models_and_services()
    assert res["overall_status"] == "degraded"
    cube = res["services"][0]
    assert cube["status"] == "error" and "unreachable" in cube["error"]


def test_cube_meta_with_views_is_ok(monkeypatch):
    seen = {}

    def meta(**kwargs):
        seen.update(kwargs)
        return {"cubes": [{"name": "pnl"}, {"name": "sales"}]}

    monkeypatch.setattr(health.cube_client, "meta", meta)
    res = health.check_cube()
    assert res["status"] == "ok" and res["http_code"] == 200
    assert seen["organization_ids"] == [0]   # schema only: no organization's data is in scope


@patch("gemini_brain.api.routes.check_all_models_and_services")
def test_get_health_models_endpoint(mock_check):
    mock_check.return_value = {
        "overall_status": "ok",
        "summary": {"models_tested": 1, "models_healthy": 1, "services_tested": 2, "services_healthy": 2},
        "models": [MODEL_OK],
        "services": [CUBE_OK, DB_OK],
    }
    response = client.get("/api/v1/health/models")
    assert response.status_code == 200
    data = response.json()
    assert data["overall_status"] == "ok"
    assert len(data["models"]) == 1 and len(data["services"]) == 2
