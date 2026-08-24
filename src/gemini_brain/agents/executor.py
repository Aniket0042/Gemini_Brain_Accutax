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
    """Reject any SQL containing a write/DDL keyword outside of string literals."""
    forbidden = ["insert", "update", "delete", "drop", "alter", "truncate"]
    stripped = re.sub(r"'[^']*'", "''", sql.lower())
    for word in forbidden:
        if re.search(r"\b" + word + r"\b", stripped):
            raise ValueError(f"Forbidden SQL operation detected: {word}")


def execute_sql(sql: str) -> tuple[list[str], list[tuple]]:
    """Execute read-only SQL, returning (columns, rows) -- matches the legacy
    agents/executor.py contract that finance_agent.py and schema_agent.py expect."""
    assert_read_only(sql)

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        if cur.description is None:
            return [], []
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        return columns, rows
    finally:
        cur.close()
        conn.close()
