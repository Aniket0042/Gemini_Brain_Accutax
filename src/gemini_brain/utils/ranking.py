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
