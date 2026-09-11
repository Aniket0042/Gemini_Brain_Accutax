"""
model_health_checker.py — Diagnostic utility to test and monitor all configured AI models and services.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests
import psycopg2

from gemini_brain.config.constants import GEMINI_MODEL
from gemini_brain.config.settings import settings
from gemini_brain.reasoning.bedrock_client import BedrockAdapter

logger = logging.getLogger("gemini_brain.health.model_health_checker")


def check_gemini_model(test_prompt: str = "Respond with 'OK'") -> Dict[str, Any]:
    """Test connection and output generation from Google Gemini 2.5 Flash."""
    t0 = time.time()
    model_name = "Google Gemini 2.5 Flash"
    model_id = GEMINI_MODEL

    if not settings.gemini_api_key:
        return {
            "name": model_name,
            "model_id": model_id,
            "provider": "Google GenAI",
            "status": "error",
            "latency_ms": 0,
            "sample_response": None,
            "error": "GEMINI_API_KEY is not configured.",
        }

    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        resp = client.models.generate_content(
            model=model_id,
            contents=test_prompt,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        output_text = (resp.text or "").strip()
        return {
            "name": model_name,
            "model_id": model_id,
            "provider": "Google GenAI",
            "status": "ok",
            "latency_ms": elapsed_ms,
            "sample_response": output_text,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.error("Gemini health check failed: %s", e)
        return {
            "name": model_name,
            "model_id": model_id,
            "provider": "Google GenAI",
            "status": "error",
            "latency_ms": elapsed_ms,
            "sample_response": None,
            "error": str(e),
        }


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
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "name": name,
            "model_id": model_id,
            "provider": "AWS Bedrock",
            "status": "ok",
            "latency_ms": elapsed_ms,
            "sample_response": output_text,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.error("Bedrock model %s health check failed: %s", model_id, e)
        return {
            "name": name,
            "model_id": model_id,
            "provider": "AWS Bedrock",
            "status": "error",
            "latency_ms": elapsed_ms,
            "sample_response": None,
            "error": str(e),
        }


def check_accutax_api() -> Dict[str, Any]:
    """Test connection to the Accutax REST API backend."""
    t0 = time.time()
    target_url = settings.accutax_base_url.rstrip("/")
    try:
        resp = requests.get(target_url, timeout=3.0)
        elapsed_ms = int((time.time() - t0) * 1000)
        is_ok = resp.status_code < 500
        return {
            "service": "Accutax REST API",
            "target": target_url,
            "status": "ok" if is_ok else "error",
            "http_code": resp.status_code,
            "latency_ms": elapsed_ms,
            "error": None if is_ok else f"HTTP {resp.status_code}",
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "service": "Accutax REST API",
            "target": target_url,
            "status": "error",
            "http_code": None,
            "latency_ms": elapsed_ms,
            "error": str(e),
        }


def check_postgres_db() -> Dict[str, Any]:
    """Test connection to the PostgreSQL database."""
    t0 = time.time()
    conn_str = (
        f"host={settings.db_host} port={settings.db_port} "
        f"dbname={settings.db_name} user={settings.db_user} "
        f"password={settings.db_password or ''}"
    )
    try:
        conn = psycopg2.connect(conn_str, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        conn.close()
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "service": "PostgreSQL Database Engine",
            "target": f"{settings.db_host}:{settings.db_port}/{settings.db_name}",
            "status": "ok",
            "latency_ms": elapsed_ms,
            "error": None,
        }
    except Exception as e:
        elapsed_ms = int((time.time() - t0) * 1000)
        return {
            "service": "PostgreSQL Database Engine",
            "target": f"{settings.db_host}:{settings.db_port}/{settings.db_name}",
            "status": "error",
            "latency_ms": elapsed_ms,
            "error": str(e),
        }


def check_all_models_and_services(test_prompt: str = "Respond with 'OK'") -> Dict[str, Any]:
    """Run health checks across all AI models, REST API, and PostgreSQL database."""
    logger.info("Executing comprehensive model and service health diagnostics...")

    # 1. AI Models Diagnostics
    models_status = [
        check_gemini_model(test_prompt),
        check_bedrock_model(settings.bedrock_model_id, "AWS Bedrock Claude 3.5 Sonnet", test_prompt),
        check_bedrock_model(settings.bedrock_model_id_fast, "AWS Bedrock Claude 3 Haiku", test_prompt),
    ]

    # 2. Services Diagnostics
    services_status = [
        check_accutax_api(),
        check_postgres_db(),
    ]

    all_models_ok = all(m["status"] == "ok" for m in models_status)
    all_services_ok = all(s["status"] == "ok" for s in services_status)
    overall_status = "ok" if (all_models_ok and all_services_ok) else "degraded"

    return {
        "overall_status": overall_status,
        "summary": {
            "models_tested": len(models_status),
            "models_healthy": sum(1 for m in models_status if m["status"] == "ok"),
            "services_tested": len(services_status),
            "services_healthy": sum(1 for s in services_status if s["status"] == "ok"),
        },
        "models": models_status,
        "services": services_status,
    }
