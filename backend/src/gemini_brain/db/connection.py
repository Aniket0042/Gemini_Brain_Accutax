"""
connection.py — PostgreSQL connection provider for logins, chat memory and reports.

Moved from sql_fallback/db_connection.py.
Uses ContextVar for async-safe per-request database selection.
Hardened in Phase 1 with statement timeouts and resilient outcome return values.
"""
from __future__ import annotations

import logging
import threading
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2 import extensions

from gemini_brain.config.settings import settings

if TYPE_CHECKING:
    from gemini_brain.resilience.outcomes import Retrieved

logger = logging.getLogger("gemini_brain.db.connection")

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


def _resolve_db(db_name: str) -> str:
    requested = db_name if (db_name and db_name != _PLACEHOLDER_DB) else (active_dbname.get() or "")
    resolved = requested or settings.db_name

    if resolved not in _allowed_databases():
        logger.warning(
            "Rejected database override %r; falling back to the configured "
            "database %r. Add it to DB_NAME_ALLOWLIST to permit it.",
            resolved, settings.db_name,
        )
        resolved = settings.db_name
    return resolved


def _connect_kwargs(dbname: str) -> Dict[str, Any]:
    return dict(
        host=settings.db_host,
        port=settings.db_port,
        dbname=dbname,
        user=settings.db_user,
        password=settings.db_password,
        connect_timeout=3,
        options="-c statement_timeout=20000",   # matches constants.SQL_TIMEOUT_MS
        # Find connections the server or a VPN hop dropped while pooled.
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )


# ── Connection pool ──────────────────────────────────────────────────────────
# A new connection cost ~0.4 s plus several round trips per query (measured
# over the VPN, 6 Oct 2026); the SQL itself was usually faster than that.
# Callers keep their `conn = get_connection()` ... `conn.close()` shape:
# close() hands the connection back, rolled back to a clean state.
#
# psycopg2's own pool keeps a returned connection only while fewer than
# `minconn` sit idle, so with lazy creation (minconn=0) it closes every one.
# This pool opens connections on demand and keeps up to db_pool_max.

#: A pooled connection idle longer than this is pinged before reuse.
_PING_AFTER_IDLE_SECONDS = 60.0


class _Pool:
    def __init__(self, dbname: str, maxconn: int):
        self.dbname = dbname
        self.maxconn = max(1, maxconn)
        self.idle: List[Tuple[Any, float]] = []  # (connection, released at)
        self.in_use = 0
        self.lock = threading.Lock()

    def get(self) -> Optional[Any]:
        """A live connection, or None when all `maxconn` are in use."""
        while True:
            with self.lock:
                if self.idle:
                    conn, released_at = self.idle.pop()
                elif self.in_use < self.maxconn:
                    conn, released_at = None, 0.0
                else:
                    return None
                self.in_use += 1
            if conn is None:
                try:
                    return psycopg2.connect(**_connect_kwargs(self.dbname))
                except Exception:
                    with self.lock:
                        self.in_use -= 1
                    raise
            if _alive(conn, released_at):
                return conn
            _close_quietly(conn)
            with self.lock:
                self.in_use -= 1

    def put(self, conn: Any, broken: bool) -> None:
        with self.lock:
            self.in_use -= 1
            if not broken and not conn.closed and len(self.idle) < self.maxconn:
                self.idle.append((conn, time.monotonic()))
                return
        _close_quietly(conn)

    def close_all(self) -> None:
        with self.lock:
            idle, self.idle = self.idle, []
        for conn, _ in idle:
            _close_quietly(conn)


_pools: Dict[str, _Pool] = {}
_pools_lock = threading.Lock()


def _pool_for(dbname: str) -> _Pool:
    with _pools_lock:
        p = _pools.get(dbname)
        if p is None:
            p = _pools[dbname] = _Pool(dbname, settings.db_pool_max)
        return p


def close_pools() -> None:
    """Close every idle pooled connection (shutdown, tests)."""
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for p in pools:
        p.close_all()


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _alive(conn: Any, released_at: float) -> bool:
    """A cheap check for a connection that sat idle in the pool."""
    if conn.closed:
        return False
    if time.monotonic() - released_at < _PING_AFTER_IDLE_SECONDS:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
        return True
    except Exception:
        return False


class PooledConnection:
    """A psycopg2 connection on loan from the pool; close() returns it.

    Everything else passes through to the real connection. On return, an open
    or failed transaction is rolled back (as closing a plain connection would
    discard it) and autocommit is reset; a broken connection is discarded.
    """

    __slots__ = ("_conn", "_pool", "_released")

    def __init__(self, conn: Any, pool: Optional[_Pool]):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_released", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._conn, name, value)

    def __enter__(self) -> "PooledConnection":
        self._conn.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._conn.__exit__(*exc)

    def close(self) -> None:
        if self._released:
            return
        object.__setattr__(self, "_released", True)
        conn, pool = self._conn, self._pool
        if pool is None:  # a direct connection, opened when the pool was full
            _close_quietly(conn)
            return
        broken = bool(conn.closed)
        if not broken:
            try:
                status = conn.get_transaction_status()
                if status in (extensions.TRANSACTION_STATUS_INTRANS, extensions.TRANSACTION_STATUS_INERROR):
                    conn.rollback()
                elif status != extensions.TRANSACTION_STATUS_IDLE:
                    broken = True  # a command still running, or the link is gone
                if not broken and conn.autocommit:
                    conn.autocommit = False
            except Exception:
                broken = True
        pool.put(conn, broken)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def get_connection(db_name: str = "") -> Any:
    """Return a psycopg2 connection (pooled unless DB_POOL_ENABLED is off).

    ``db_name`` reaches this function from the request body, so it is checked
    against an allowlist rather than passed through — otherwise any caller
    could point the engine at an arbitrary database on the same host.
    """
    resolved = _resolve_db(db_name)
    if not settings.db_pool_enabled:
        return psycopg2.connect(**_connect_kwargs(resolved))
    pool = _pool_for(resolved)
    conn = pool.get()
    if conn is None:
        # Every pooled connection is in use: serve this caller directly rather than fail or wait.
        logger.info("DB pool for %s is full (%d); opening a direct connection", resolved, pool.maxconn)
        return PooledConnection(psycopg2.connect(**_connect_kwargs(resolved)), None)
    return PooledConnection(conn, pool)


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
    from gemini_brain.observability.sql_tracer import record_sql_trace
    import time

    t0 = time.perf_counter()
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
                record_sql_trace(query, duration_ms=(time.perf_counter() - t0) * 1000, row_count=0, source="sql_function")
                return []

            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            conn.commit()

            serialized = [dict(zip(cols, row)) for row in rows]
            record_sql_trace(query, duration_ms=(time.perf_counter() - t0) * 1000, row_count=len(serialized), source="sql_function")
            return serialized
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
