"""
ranking.py — shared count + direction resolution for every "top N" / "bottom N"
ranked-list query in the app.

Why this exists: every ranked report/task used to hand-roll its own row count
(hardcoded LIMIT, or none at all) and always ordered DESC — there was no way
for "bottom 5" or "least profitable" to ever come back correct, and adding a
row limit was a fresh chance to get it wrong in every new function. Centralizing
both concerns here makes it a fix-once problem: every caller reads count and
direction the same way, and gets it right by construction.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Pattern, Tuple

#: Words that mean "lowest/fewest first" when found in the user's own phrasing.
ASCENDING_HINTS: Pattern = re.compile(
    r"\b(bottom|least|lowest|smallest|worst|fewest|minimum|oldest)\b", re.IGNORECASE
)
#: Words that mean "highest/most first" — the default, but matched so an
#: ascending hint elsewhere in the same sentence (e.g. "the oldest of our top
#: 5 customers") doesn't flip direction on a false positive.
DESCENDING_HINTS: Pattern = re.compile(
    r"\b(top|highest|largest|biggest|most|greatest|best|maximum|newest)\b", re.IGNORECASE
)

#: Values accepted in a structured `sort_order` param, however the router (or
#: a caller) chooses to spell "ascending" / "descending".
_ASCENDING_VALUES = {"asc", "ascending", "bottom", "lowest", "least", "smallest", "worst"}
_DESCENDING_VALUES = {"desc", "descending", "top", "highest", "most", "largest", "best"}


def resolve_limit(params: Dict[str, Any], default: int = 20, ceiling: int = 50) -> int:
    """Bound a caller-supplied row count — it reaches us from model output."""
    try:
        value = int(params.get("limit", default))
    except (TypeError, ValueError):
        return default
    return max(1, min(value, ceiling))


def resolve_direction(params: Dict[str, Any], raw_query: str = "") -> bool:
    """Return True for ascending ("bottom/least/lowest"), False for descending.

    Prefers an explicit `sort_order` param (set by the router from a Pydantic
    field) over sniffing free text, and only falls back to `raw_query` when no
    structured param is present — the empty-result SQL verifier path has no
    structured params at all, only the user's original query text.
    """
    sort_order = str(params.get("sort_order", "") or "").strip().lower()
    if sort_order in _ASCENDING_VALUES:
        return True
    if sort_order in _DESCENDING_VALUES:
        return False

    text = raw_query or str(params.get("query", "") or "")
    if text and ASCENDING_HINTS.search(text) and not DESCENDING_HINTS.search(text):
        return True
    return False


def resolve_limit_and_direction(
    params: Dict[str, Any], raw_query: str = "", default: int = 20, ceiling: int = 50
) -> Tuple[int, bool]:
    """Convenience wrapper returning (limit, ascending) in one call."""
    return resolve_limit(params, default, ceiling), resolve_direction(params, raw_query)


#: Matches an explicit count in free-form query text: "top 5", "bottom 10",
#: "first 3", "5 largest", etc. First capture group is always the count.
_EXPLICIT_COUNT: Pattern = re.compile(
    r"\b(?:top|bottom|first|last)\s+(\d+)\b|\b(\d+)\s+(?:largest|smallest|highest|lowest|biggest|top|most|least)\b",
    re.IGNORECASE,
)

#: Phrasing that asks for a single entity ("the largest debtor", "who is our
#: biggest customer", "which vendor spent the most") with no explicit number —
#: these should render as one row, not a default-sized list.
_SINGULAR_ASK: Pattern = re.compile(
    r"\b(?:the|our|who\s+is|which)\b[^.?!]{0,40}\b(largest|biggest|smallest|highest|lowest|most|least|top|worst|best)\b(?!\s+\d)",
    re.IGNORECASE,
)


def extract_requested_count(query: str, default: int = 20, ceiling: int = 50) -> int:
    """Recover how many rows a table should show from the user's own phrasing.

    Every table-rendering call site used to hand the full result set to a flat
    default (50) regardless of what was actually asked — "top 5 overdue
    invoices" rendered 50 rows, "who is our largest debtor" rendered the whole
    aging list. This is the single place that maps free-form query text to a
    row count, so every renderer gets it right by construction:

    - An explicit number ("top 5", "5 largest") wins outright.
    - A singular ask with no number ("the largest debtor", "who is our biggest
      customer") means exactly one row.
    - Otherwise, the caller's own default stands unchanged — a plain "list
      invoices" keeps showing a normal-sized page.
    """
    if not isinstance(query, str) or not query:
        return default
    m = _EXPLICIT_COUNT.search(query)
    if m:
        raw = m.group(1) or m.group(2)
        try:
            return max(1, min(int(raw), ceiling))
        except (TypeError, ValueError):
            return default
    if _SINGULAR_ASK.search(query):
        return 1
    return default


def order_sql(ascending: bool) -> str:
    """SQL keyword for a resolved direction. Never derived from user input
    directly — always from the bool this module computed — so it's safe to
    interpolate into an ORDER BY clause."""
    return "ASC" if ascending else "DESC"


def extract_limit_from_text(raw_query: Any, patterns: List[Pattern], default: int = 10) -> int:
    """Recover a 'top N' count from free-form query text by re-matching a
    rule's own patterns against it (each pattern's first capture group must
    be the count, e.g. `top\\s+(\\d+)\\s+vendors?`).

    Used where no structured params dict is available — see
    router.rules.get_endpoint_sql_verifiers.
    """
    if not isinstance(raw_query, str) or not raw_query:
        return default
    for p in patterns:
        m = p.search(raw_query)
        if m and m.groups():
            try:
                v = m.group(1)
                return int(v) if v else default
            except (IndexError, TypeError, ValueError):
                continue
    return default


def extract_direction_from_text(raw_query: Any) -> bool:
    """Same idea as extract_limit_from_text, but for direction: sniff
    ascending/descending hint words directly out of free-form query text."""
    if not isinstance(raw_query, str) or not raw_query:
        return False
    if ASCENDING_HINTS.search(raw_query) and not DESCENDING_HINTS.search(raw_query):
        return True
    return False
