"""outcomes.py — Explicit retrieval outcome classification.

Replaces the ambiguous `data is not None` check with a three-axis classification:
reachability, emptiness, and payload trust.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Outcome(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"
    INVALID = "invalid"


#: Keys commonly used by upstream envelopes to wrap the real row list.
LIST_WRAPPER_KEYS = (
    "items", "results", "data", "rows", "records",
    "invoices", "bills", "transactions", "contacts", "accounts", "entries",
)

#: Keys that are pure metadata — an object containing only these is not real data.
METADATA_ONLY_KEYS = frozenset({
    "success", "status", "message", "code", "error", "errors",
    "total", "count", "page", "page_size", "limit", "offset",
    "timestamp", "request_id",
})


@dataclass
class Retrieved:
    """The result of one retrieval attempt against one tier."""
    outcome: Outcome
    payload: Any = None
    rows: list = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    tier: str = ""                       # live_api | cache | sql_function | sql_fallback
    endpoint: str = ""
    reason: str = ""                     # short machine-ish reason, e.g. "http_503"
    detail: str = ""                     # operator-only detail; never shown to users
    http_status: Optional[int] = None

    @property
    def usable(self) -> bool:
        """True when the payload can be narrated (has at least one row/value)."""
        return self.outcome in (Outcome.OK, Outcome.PARTIAL)

    @property
    def reached_source(self) -> bool:
        """True when the source answered, even if with zero rows."""
        return self.outcome in (Outcome.OK, Outcome.PARTIAL, Outcome.EMPTY)

    def to_data_source(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "endpoint": self.endpoint,
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return True
    # A currency-formatted zero ("0.00 AED", "AED 0.00") is blank too — it's the
    # same fact as the raw 0 it mirrors, just pre-formatted by the upstream API.
    numeric = re.sub(r"[^\d.\-]", "", s)
    if numeric and numeric not in ("-", ".", "-."):
        try:
            return float(numeric) == 0
        except ValueError:
            pass
    return False


#: Keys that describe the *scope* of a request (what date range was searched),
#: not its *content*. These are present on every response, including genuinely
#: empty ones, so a populated "period" must never count as evidence of real
#: data — otherwise {"vendors": [], "period": {"start_date": "2025-01-01", ...}}
#: looks non-blank purely because the date strings aren't empty, and a
#: genuinely empty report gets narrated by an LLM instead of answered
#: deterministically.
_SCOPE_METADATA_KEYS = frozenset({"period", "date_range", "filters"})


def _all_values_blank(d: Dict[str, Any]) -> bool:
    """A summary object of all zeros/nulls is 'empty' for reporting purposes.

    Recurses into nested dicts so a container like {"totals": {"aging_buckets":
    {...all zero...}}} is correctly seen as blank — a non-empty *wrapper* around
    all-zero figures is not itself evidence of real data. Scope-metadata keys
    (see _SCOPE_METADATA_KEYS) are skipped entirely for the same reason.
    """
    if not d:
        return True
    for k, v in d.items():
        if k in _SCOPE_METADATA_KEYS:
            continue
        if isinstance(v, dict):
            if not _all_values_blank(v):
                return False
        elif isinstance(v, list):
            if v:
                return False
        elif isinstance(v, bool):
            return False
        elif isinstance(v, (int, float)):
            if v != 0:
                return False
        elif not _is_blank(v):
            return False
    return True


def classify_payload(
    payload: Any,
    *,
    tier: str = "",
    endpoint: str = "",
    truncated: bool = False,
) -> Retrieved:
    """Classify a decoded payload into an Outcome. Never raises.

    Rules, in order:
      1. None / blank string          -> INVALID  (source gave us nothing usable)
      2. Explicit failure envelope    -> INVALID
      3. list                         -> EMPTY if len == 0 else OK
      4. dict with a wrapper key      -> recurse on the wrapped list
      5. dict of metadata only        -> EMPTY
      6. dict all-zero / all-null     -> EMPTY
      7. dict                         -> OK (single-object summary)
      8. scalar                       -> OK
    """
    if payload is None or (isinstance(payload, str) and not payload.strip()):
        return Retrieved(Outcome.INVALID, tier=tier, endpoint=endpoint, reason="null_payload")

    if isinstance(payload, dict):
        # 2. explicit failure envelope
        if payload.get("success") is False or payload.get("status") in ("error", "failed"):
            return Retrieved(
                Outcome.INVALID, payload=payload, tier=tier, endpoint=endpoint,
                reason="upstream_error_envelope",
                detail=str(payload.get("message") or payload.get("error") or "")[:300],
            )
        if "error" in payload and payload.get("error"):
            return Retrieved(
                Outcome.INVALID, payload=payload, tier=tier, endpoint=endpoint,
                reason="upstream_error_field", detail=str(payload["error"])[:300],
            )
        # 4. wrapper key
        for key in LIST_WRAPPER_KEYS:
            inner = payload.get(key)
            if isinstance(inner, list):
                inner_res = classify_payload(inner, tier=tier, endpoint=endpoint, truncated=truncated)
                inner_res.payload = payload          # keep the full envelope for the formatter
                return inner_res
        # 5. metadata only
        if set(payload.keys()) <= METADATA_ONLY_KEYS:
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="metadata_only")
        # 6. all zero / all null
        if _all_values_blank(payload):
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="all_values_zero_or_null")
        # 7. single-object summary (e.g. {"net_profit": 15000, "revenue": 90000})
        return Retrieved(
            Outcome.PARTIAL if truncated else Outcome.OK,
            payload=payload, rows=[payload], row_count=1,
            tier=tier, endpoint=endpoint, truncated=truncated,
        )

    if isinstance(payload, list):
        # 3.
        if len(payload) == 0:
            return Retrieved(Outcome.EMPTY, payload=payload, tier=tier, endpoint=endpoint,
                             reason="zero_rows")
        return Retrieved(
            Outcome.PARTIAL if truncated else Outcome.OK,
            payload=payload, rows=payload, row_count=len(payload),
            tier=tier, endpoint=endpoint, truncated=truncated,
        )

    # 8. scalar (int/float/bool/str)
    return Retrieved(Outcome.OK, payload=payload, rows=[payload], row_count=1,
                     tier=tier, endpoint=endpoint, truncated=truncated)
