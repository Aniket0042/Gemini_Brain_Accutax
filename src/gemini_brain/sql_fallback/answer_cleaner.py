"""
answer_cleaner.py — Output response cleaning and garbage-phrase detection.

Extracted from engine.py lines 848-964 (_GARBAGE_PHRASES, _VALID_STARTS, _is_garbage_answer, _clean_thinking_artifacts).
Detects model thinking-out-loud artifacts and cleans output text.
"""
from __future__ import annotations

import re

# ── Verbatim Garbage Phrases & Valid Starts ─────────────────────────────────
GARBAGE_PHRASES: list[str] = [
    "let me try", "still an issue", "hmm,", "i'll need to", "correct parameter",
    "let me query", "let me search", "now let me", "i'm going to try",
    "let me attempt", "let me run", "let me check", "where is the rest",
    "let me look", "i need to", "let me also", "let me now",
    "the results are still", "results are truncated", "results are still",
    "data is truncated", "still getting an error", "i'm still", "still an error",
    "let me request", "let me re-run", "let me use", "let me reformulate",
    "let me reconsider", "let me simplify", "let me rethink", "let me revise",
    "let me adjust", "let me modify", "let me construct", "let me build",
    "let me write", "let me create a", "let me execute", "let me calculate",
    "apologies,", "i apologize,", "unfortunately,", "i encountered",
    "it seems the", "it appears the",
    "the query searches", "this query searches", "the sql query", "this sql query",
    "the above query", "the following query", "the query retrieves",
    "this query retrieves", "the query selects", "this query selects",
    "the query looks", "this query looks", "the query finds", "this query finds",
    "the query filters", "this query filters", "the query returns all",
    "the query returns entries", "this returns all", "this query returns",
    "the above sql", "the above select", "this sql searches",
    "the executed query", "the database query", "a sql query",
    "the search query", "this search query",
    "the query above", "this query above",
    "this searches the", "this retrieves", "this selects",
    "the query is searching", "the query is filtering",
    "it searches the", "it retrieves the", "it filters the",
    "the result of the query", "the results of the query",
    "this query was", "the query was",
    "the key details shown", "the key details for", "the key fields",
    "the following fields are", "the following fields include",
    "each result contains the", "each record contains the",
    "the results include the following", "the data includes the following",
    "the result set contains", "the following key details",
    "each entry contains the", "each journal entry contains the",
    "the columns include", "the columns are", "the column details",
    "provides a comprehensive view", "provides an overview of the",
    "this provides a comprehensive", "this gives a comprehensive",
    "the query result shows the", "the output includes",
]

VALID_STARTS: list[str] = [
    "unfortunately, the database", "unfortunately, no", "unfortunately, there are no",
    "unfortunately, this information", "i apologize, but the database",
    "i cannot identify", "i cannot provide", "based on the analysis",
    "based on the data", "the data shows", "the results show",
]


def is_garbage_answer(text: str) -> bool:
    """Detect if an answer is model thinking-out-loud rather than a real answer."""
    if not text:
        return True
    low = text.lower().strip()

    # Check valid-start exceptions first (legitimate negative answers)
    for vs in VALID_STARTS:
        if low.startswith(vs):
            return False

    # Answer ending with colon means cut off mid-thought
    if low.rstrip().endswith(":"):
        return True

    # If the answer STARTS with a garbage phrase, it's thinking
    for phrase in GARBAGE_PHRASES:
        if low.startswith(phrase):
            return True

    # Structural detection: bullet list of field_name: definition (schema dump)
    lines = [l.strip().lstrip("- •*") for l in text.split("\n") if l.strip()]
    field_def_lines = sum(
        1 for l in lines
        if re.match(r"^[a-z][a-z0-9_]{2,}:\s+[A-Z]", l)
    )
    if field_def_lines >= 3:
        return True

    # For short answers (< 200 chars), any garbage phrase in the text flags it
    if len(low) < 200:
        for phrase in GARBAGE_PHRASES:
            if phrase in low:
                return True

    # Multiple garbage phrases in any length = thinking out loud
    count = sum(1 for p in GARBAGE_PHRASES if p in low)
    if count >= 2:
        return True

    return False


def clean_thinking_artifacts(text: str) -> str:
    """Remove thinking-out-loud prefixes from model answers."""
    lines = text.split("\n")
    cleaned = []
    started_real_content = False
    for line in lines:
        low = line.strip().lower()
        if not started_real_content:
            # Skip lines that are clearly thinking
            if any(low.startswith(p) for p in GARBAGE_PHRASES):
                continue
            if low in ("", "okay.", "alright.", "sure."):
                continue
            started_real_content = True
        cleaned.append(line)
    return "\n".join(cleaned).strip() if cleaned else text
