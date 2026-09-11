"""
db_connection.py — PostgreSQL connection provider for SQL fallback & memory services.

Extracted from executor.py lines 1-35.
Uses ContextVar for async-safe per-request database selection.
Hardened in Phase 1 with statement timeouts and resilient outcome return values.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Optional

import psycopg2

from gemini_brain.config.settings import settings

if TYPE_CHECKING:
    from gemini_brain.resilience.outcomes import Retrieved

logger = logging.getLogger("gemini_brain.sql_fallback.db_connection")

# ContextVar that lets API layer select a different DB per-request (async-safe)
active_dbname: ContextVar[str] = ContextVar("active_dbname", default="")


#: Legacy placeholder the API and UI still send as their default db_name; it
#: means "use whatever is configured", not a real database.
_PLACEHOLDER_DB = "accutax_bk"


def _allowed_databases() -> set[str]:
    """Databases a request may select. Configured one plus any explicit extras."""
    extra = {
        name.strip()
        for name in (settings.db_name_allowlist or "").split(",")
        if name.strip()
    }
    return {settings.db_name} | extra


def get_connection(db_name: str = "") -> Any:
    """Return a psycopg2 connection.

    ``db_name`` reaches this function from the request body, so it is checked
    against an allowlist rather than passed through — otherwise any caller
    could point the engine at an arbitrary database on the same host.
    """
    requested = db_name if (db_name and db_name != _PLACEHOLDER_DB) else (active_dbname.get() or "")
    resolved = requested or settings.db_name

    if resolved not in _allowed_databases():
        logger.warning(
            "Rejected database override %r; falling back to the configured "
            "database %r. Add it to DB_NAME_ALLOWLIST to permit it.",
            resolved, settings.db_name,
        )
        resolved = settings.db_name

    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=resolved,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=3,
        options="-c statement_timeout=20000",   # matches constants.SQL_TIMEOUT_MS
    )


#: Cache of org-id → exists, so the check costs one query per org per process.
_ORG_EXISTS_CACHE: dict[int, bool] = {}


def organization_exists(org_id: int, db_name: str = "") -> Optional[bool]:
    """Whether ``org_id`` is present in the connected database.

    Returns None when the check itself could not run, so callers can tell
    "definitely absent" apart from "could not determine". This is what lets an
    empty result be reported as "this organization is not in this database"
    rather than the far more damaging "you have no data".
    """
    if org_id in _ORG_EXISTS_CACHE:
        return _ORG_EXISTS_CACHE[org_id]

    try:
        conn = get_connection(db_name=db_name)
    except Exception as e:
        logger.warning("Could not check organization %s: %s", org_id, e)
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM organizations WHERE id = %s LIMIT 1;", (int(org_id),))
            found = cur.fetchone() is not None
    except Exception as e:
        logger.warning("Could not check organization %s: %s", org_id, e)
        return None
    finally:
        conn.close()

    _ORG_EXISTS_CACHE[org_id] = found
    return found


def execute_sql_function(
    func_name: str,
    params: tuple[Any, ...],
    org_id: int,
    db_name: str = "",
) -> list[dict[str, Any]]:
    """Execute a PostgreSQL stored function with a timeout, publishing org_id
    as the `app.current_org` session variable Row-Level Security policies key
    on.

    That RLS enforcement is not active yet in every environment: it requires
    the `ai_reader` role and policies from sql/functions/005_rls_reader_role.sql
    to actually be applied, *and* this connection to authenticate as that role
    rather than the table owner (owners bypass RLS regardless of policy state).
    Until both are true, `app.current_org` is set but inert, and the only
    tenant-isolation guarantee is whatever each stored function's own SQL body
    enforces explicitly (the 3 functions in sql/functions/ all do, as of this
    writing — each has its own `organization_id = p_org` filter, independent
    of this session variable).

    Parameters
    ----------
    func_name : str
        Name of the function (e.g. 'fn_project_expense_rollup').
    params : tuple
        Positional parameters for the function.
    org_id : int
        Current organization ID for the app.current_org session variable.
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
