"""
executor.py — thin compatibility shim for the migrated agents/ package.

The original monolith's agents/finance_agent.py and agents/schema_agent.py
import `execute_sql` and `get_connection` from a top-level `executor.py`.
Rather than duplicating a second DB connection implementation with its own
env var reads, this delegates to gemini_brain.sql_fallback.db_connection,
which already reads the same DB_HOST/PORT/NAME/USER/PASSWORD settings this
app uses everywhere else (including the SSH-tunneled connection on deploy).
"""
from __future__ import annotations

import re

from gemini_brain.sql_fallback.db_connection import get_connection

__all__ = ["get_connection", "assert_read_only", "execute_sql"]


def assert_read_only(sql: str) -> None:
    """Reject any SQL that is not a single read-only statement."""
    forbidden = [
        "insert", "update", "delete", "drop", "alter", "truncate",
        # DO blocks and CALL run arbitrary procedural code; COPY ... TO PROGRAM
        # and the pg_*_file functions reach the host filesystem; the rest write
        # or change permissions.
        "do", "call", "copy", "grant", "revoke", "create", "merge",
        "vacuum", "reindex", "refresh", "lock", "prepare", "execute",
        "pg_read_file", "pg_ls_dir", "pg_sleep", "dblink", "lo_import",
    ]
    stripped = re.sub(r"'[^']*'", "''", sql.lower())
    stripped = re.sub(r"\$\$.*?\$\$", "''", stripped, flags=re.DOTALL)

    for word in forbidden:
        if re.search(r"\b" + re.escape(word) + r"\b", stripped):
            raise ValueError(f"Forbidden SQL operation detected: {word}")

    # One statement only — a trailing semicolon is fine, a second statement is not.
    if len([part for part in stripped.split(";") if part.strip()]) > 1:
        raise ValueError("Only a single SQL statement may be executed")

    if not re.match(r"^\s*(with|select)\b", stripped):
        raise ValueError("Only SELECT/WITH statements may be executed")


def execute_sql(sql: str, org_id: int | None = None) -> tuple[list[str], list[tuple]]:
    """Execute read-only SQL, returning (columns, rows) -- matches the legacy
    agents/executor.py contract that finance_agent.py and schema_agent.py expect.

    When ``org_id`` is given it is published as ``app.current_org`` so the
    row-level security policies from 004_rls_hardening.sql apply. That makes a
    query which forgot its organization_id filter return nothing, rather than
    another tenant's rows.
    """
    assert_read_only(sql)

    conn = get_connection()
    cur = conn.cursor()
    try:
        if org_id is not None:
            cur.execute("SET LOCAL app.current_org = %s;", (str(int(org_id)),))
        cur.execute(sql)
        if cur.description is None:
            return [], []
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return columns, rows
    finally:
        cur.close()
        conn.close()
