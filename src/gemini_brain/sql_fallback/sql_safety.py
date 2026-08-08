"""
sql_safety.py — SQL read-only validation.

Extracted from executor.py lines 39-58 (assert_read_only).
Prevents write/mutation operations in generated SQL queries.
"""
from __future__ import annotations

import re


def assert_read_only(sql: str) -> None:
    """Validate that the SQL string contains no forbidden write operations.

    Strips string literals first so that data values like 'update' in
    WHERE ... LIKE '%update%' don't trigger false positives.

    Raises
    ------
    ValueError
        If a forbidden operation keyword (insert, update, delete, etc.) is found.
    """
    forbidden = ["insert", "update", "delete", "drop", "alter", "truncate"]
    # Strip single-quoted string literals to avoid false positives
    stripped = re.sub(r"'[^']*'", "''", sql.lower())

    for word in forbidden:
        pattern = r"\b" + word + r"\b"
        if re.search(pattern, stripped):
            raise ValueError(f"Forbidden SQL operation detected: {word}")
