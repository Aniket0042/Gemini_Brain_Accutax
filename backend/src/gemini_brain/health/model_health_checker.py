"""
model_health_checker.py — Health of what the agent depends on: its Bedrock model(s), Cube and PostgreSQL.

Serves GET/POST /api/v1/health/models. The response shape (models + services) is the one the
frontend's model-health modal reads.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import psycopg2

from gemini_brain.config.constants import SONNET5_ID, model_label
from gemini_brain.config.settings import settings
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.semantic import cube_client

logger = logging.getLogger("gemini_brain.health.model_health_checker")

#: Cube's meta call needs an organization scope in its token. Meta returns the schema only, never
#: rows, so a scope that matches no organization is enough.
_META_SCOPE = [0]
_CUBE_TIMEOUT_SECONDS = 10.0


def _elapsed_ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def agent_model_ids() -> List[str]:
    """The Bedrock models the agent calls: the planner, and the answer writer when it differs."""
    planner = settings.agent_model_id or SONNET5_ID
    writer = settings.agent_answer_model_id or planner
    return [planner] if writer == planner else [planner, writer]


def check_bedrock_model(
    model_id: str,
    name: str,
    test_prompt: str = "Respond with 'OK'",
) -> Dict[str, Any]:
    """Test connection and text generation from an AWS Bedrock Claude model."""
    t0 = time.time()
    try:
        adapter = BedrockAdapter(model_id=model_id, label=name)
        output_text = adapter.converse(
            system_prompt="You are a system health check assistant. Be extremely concise.",
            messages=[{"role": "user", "content": [{"text": test_prompt}]}],
            max_tokens=50,
        ).strip()
        return {
            "name": name,
            "model_id": model_id,
            "provider": "AWS Bedrock",
            "status": "ok",
            "latency_ms": _elapsed_ms(t0),
            "sample_response": output_text,
            "error": None,
        }
    except Exception as e:
        logger.error("Bedrock model %s health check failed: %s", model_id, e)
        return {
            "name": name,
            "model_id": model_id,
            "provider": "AWS Bedrock",
            "status": "error",
            "latency_ms": _elapsed_ms(t0),
            "sample_response": None,
            "error": str(e),
        }


def check_cube() -> Dict[str, Any]:
    """Cube answers its meta call: reachable, the JWT secret matches, and the model compiled."""
    t0 = time.time()
    target = settings.cube_api_url.rstrip("/") + "/cubejs-api/v1/meta"
    try:
        body = cube_client.meta(organization_ids=_META_SCOPE, subject="health-check",
                                deadline=time.monotonic() + _CUBE_TIMEOUT_SECONDS)
        views = len(body.get("cubes") or [])
        ok = views > 0
        return {
            "service": "Cube semantic layer",
            "target": target,
            "status": "ok" if ok else "error",
            "http_code": 200,
            "latency_ms": _elapsed_ms(t0),
            "error": None if ok else "Cube returned no views",
        }
    except Exception as e:
        logger.error("Cube health check failed: %s", e)
        return {
            "service": "Cube semantic layer",
            "target": target,
            "status": "error",
            "http_code": None,
            "latency_ms": _elapsed_ms(t0),
            "error": str(e),
        }


def check_postgres_db() -> Dict[str, Any]:
    """Test connection to the PostgreSQL database (logins and chat history)."""
    t0 = time.time()
    conn_str = (
        f"host={settings.db_host} port={settings.db_port} "
        f"dbname={settings.db_name} user={settings.db_user} "
        f"password={settings.db_password or ''}"
    )
    target = f"{settings.db_host}:{settings.db_port}/{settings.db_name}"
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        conn.close()
        return {
            "service": "PostgreSQL Database Engine",
            "target": target,
            "status": "ok",
            "latency_ms": _elapsed_ms(t0),
            "error": None,
        }
    except Exception as e:
        return {
            "service": "PostgreSQL Database Engine",
            "target": target,
            "status": "error",
            "latency_ms": _elapsed_ms(t0),
            "error": str(e),
        }


def check_all_models_and_services(test_prompt: str = "Respond with 'OK'") -> Dict[str, Any]:
    """Run the health checks for the agent's model(s), Cube and PostgreSQL."""
    logger.info("Executing agent model and service health diagnostics...")

    models_status = [check_bedrock_model(model_id, f"AWS Bedrock {model_label(model_id)}", test_prompt)
                     for model_id in agent_model_ids()]
    services_status = [check_cube(), check_postgres_db()]

    all_ok = all(m["status"] == "ok" for m in models_status) and all(s["status"] == "ok" for s in services_status)
    return {
        "overall_status": "ok" if all_ok else "degraded",
        "summary": {
            "models_tested": len(models_status),
            "models_healthy": sum(1 for m in models_status if m["status"] == "ok"),
            "services_tested": len(services_status),
            "services_healthy": sum(1 for s in services_status if s["status"] == "ok"),
        },
        "models": models_status,
        "services": services_status,
    }
