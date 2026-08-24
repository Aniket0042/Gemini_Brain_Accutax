"""
db_connection.py — PostgreSQL connection provider for SQL fallback & memory services.

Extracted from executor.py lines 1-35.
Uses ContextVar for async-safe per-request database selection.
Hardened in Phase 1 with statement timeouts and resilient outcome return values.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import psycopg2

from gemini_brain.config.settings import settings

if TYPE_CHECKING:
    from gemini_brain.resilience.outcomes import Retrieved

logger = logging.getLogger("gemini_brain.sql_fallback.db_connection")

# ContextVar that lets API layer select a different DB per-request (async-safe)
active_dbname: ContextVar[str] = ContextVar("active_dbname", default="")


def get_connection(db_name: str = "") -> Any:
    """Return a psycopg2 connection.

    If db_name is given (or set via the active_dbname ContextVar) that database
    is used; otherwise the default from settings is used.
    """
    resolved = db_name if (db_name and db_name != "accutax_bk") else (active_dbname.get() or settings.db_name)
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=resolved,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=3,
        options="-c statement_timeout=20000",   # matches constants.SQL_TIMEOUT_MS
    )


def execute_sql_function(
    func_name: str,
    params: tuple[Any, ...],
    org_id: int,
    db_name: str = "",
) -> list[dict[str, Any]]:
    """Execute a PostgreSQL stored function under strict RLS isolation and timeout.

    Parameters
    ----------
    func_name : str
        Name of the function (e.g. 'fn_project_expense_rollup').
    params : tuple
        Positional parameters for the function.
    org_id : int
        Current organization ID for RLS session variable.
    db_name : str, optional
        Database override.

    Returns
    -------
    list[dict[str, Any]]
        List of row dictionaries.
    """
    conn = get_connection(db_name=db_name)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            # Set local session context variables for RLS and execution timeout
            cur.execute("SET LOCAL app.current_org = %s;", (str(org_id),))
            cur.execute("SET LOCAL statement_timeout = '10s';")

            # Format parameter placeholders %s
            placeholders = ", ".join(["%s"] * len(params))
            query = f"SELECT * FROM {func_name}({placeholders});"
            cur.execute(query, params)

            if cur.description is None:
                conn.commit()
                return []

            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            conn.commit()

            return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        conn.rollback()
        logger.error("Error executing SQL function %s with org_id=%s: %s", func_name, org_id, e)
        raise
    finally:
        conn.close()


def execute_sql_function_safe(
    func_name: str,
    params: tuple[Any, ...],
    org_id: int,
    db_name: str = "",
) -> "Retrieved":
    """Safely execute a PostgreSQL stored function and return a structured Retrieved outcome.
    
    Never raises.
    """
    from gemini_brain.resilience.outcomes import Outcome, Retrieved, classify_payload
    from gemini_brain.resilience.errors import classify_exception, ErrorCode

    try:
        rows = execute_sql_function(func_name, params, org_id, db_name=db_name)
    except Exception as e:
        code = classify_exception(e)
        outcome = Outcome.UNAVAILABLE if code == ErrorCode.DB_UNAVAILABLE else Outcome.INVALID
        logger.warning("SQL function %s failed (%s): %s", func_name, code.value, e)
        return Retrieved(
            outcome,
            tier="sql_function",
            endpoint=func_name,
            reason=code.value.lower(),
            detail=str(e)[:300],
        )
    return classify_payload(rows, tier="sql_function", endpoint=func_name)
