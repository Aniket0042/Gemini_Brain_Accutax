"""
db_connection.py — PostgreSQL connection provider for SQL fallback & memory services.

Extracted from executor.py lines 1-35.
Uses ContextVar for async-safe per-request database selection.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

import psycopg2

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.sql_fallback.db_connection")

# ContextVar that lets API layer select a different DB per-request (async-safe)
active_dbname: ContextVar[str] = ContextVar("active_dbname", default="")


def get_connection(db_name: str = "") -> Any:
    """Return a psycopg2 connection.

    If db_name is given (or set via the active_dbname ContextVar) that database
    is used; otherwise the default from settings is used.
    """
    resolved = db_name or active_dbname.get() or settings.db_name
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=resolved,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=3,
    )
