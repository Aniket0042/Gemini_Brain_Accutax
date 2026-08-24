"""output_guard.py — Deterministic last-gate filter against backend/IT-support leaks.

This is not a prompt instruction. A prompt can be ignored by the model, and this
session found a live case of exactly that: a system prompt already told the model
never to expose backend detail, and it still produced a "Status: Data Retrieval
Failed... contact your IT/Database team" style answer anyway. This module is a
plain text check with a fixed, honest fallback — it runs on every answer through
normalize_envelope() regardless of which path produced it (LEFT-path direct
answer, RIGHT-path narration, SQL-fallback engine, sync or streaming), so it
can't be skipped by whichever prompt happened to generate a given response.
"""
from __future__ import annotations

import re
from typing import Optional

#: Phrases that only show up when a model is explaining a failure like a database
#: administrator rather than a finance analyst. Deliberately specific multi-word
#: phrases, not bare words like "database" or "table" alone, to avoid flagging a
#: legitimate answer that happens to mention a business's own database/records.
_BACKEND_LEAK_SIGNALS: tuple[str, ...] = (
    "database is experiencing",
    "database load",
    "off-peak hours",
    "contact your it",
    "contact your database",
    "database administrator",
    "it/database team",
    "it department",
    "database query timed out",
    "the database query",
    "this database query",
    "query requires optimization",
    "optimize the query",
    "optimize the vendor query",
    "query performance",
    "the sql query",
    "this sql query",
    "the executed query",
    "status: data retrieval failed",
    "data retrieval failed",
)

#: Raw database error signatures — these are never appropriate in a user-facing
#: answer regardless of context, so a regex match alone is sufficient.
_BACKEND_LEAK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bcolumn\s+[\w.\"]+\s+does not exist", re.IGNORECASE),
    re.compile(r'\brelation\s+"?[\w.]+"?\s+does not exist', re.IGNORECASE),
    re.compile(r"\bsyntax error at or near\b", re.IGNORECASE),
    # Schema/field-name references — e.g. "the 'vendors' array is empty", "the
    # `totals` object contains". Caught a live case of exactly this even though
    # ANALYST_SYSTEM_PROMPT names "the vendors array" as a forbidden example
    # verbatim — the model said it anyway. Quote marks around the identifier
    # are required: both real leaks seen this session quoted the field name
    # that way. An earlier, unquoted version of this pattern (any word before
    # "array"/"object"/etc.) false-positived on ordinary financial narration
    # like "the income values array shows a spike in June" — a real numeric
    # series described in plain English, not a schema leak — and replaced a
    # correct, data-backed answer with a false "couldn't retrieve" message.
    # That's worse than the leak it was meant to catch, so precision here
    # matters more than broad recall.
    re.compile(r'["\'`][a-z_]{2,30}["\'`]\s+(array|object|field|key|property|section)\b', re.IGNORECASE),
)

_FALLBACK_MESSAGE = (
    "I wasn't able to retrieve {subject} right now. This doesn't necessarily mean "
    "there's no data - please try again in a moment, or rephrase with a narrower "
    "date range or filter."
)


def looks_like_backend_leak(text: str) -> bool:
    """True if `text` contains a database/IT-support-style leak signal."""
    if not text:
        return False
    low = text.lower()
    if any(sig in low for sig in _BACKEND_LEAK_SIGNALS):
        return True
    return any(p.search(text) for p in _BACKEND_LEAK_PATTERNS)


def sanitize_answer(text: Optional[str], subject: str = "that information") -> Optional[str]:
    """Replace `text` with a clean fallback if it leaks backend/IT-support detail.

    Returns `text` unchanged when it's clean (the common case) or falsy.
    """
    if not text:
        return text
    if not looks_like_backend_leak(text):
        return text
    return _FALLBACK_MESSAGE.format(subject=subject)
