"""
metrics.py — Thread-safe latency & operational counters for Gemini Brain.
"""
from __future__ import annotations

import logging
import threading
from typing import Dict

logger = logging.getLogger("gemini_brain.observability.metrics")


class Counter:
    """Thread-safe integer counter."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, amount: int = 1) -> int:
        with self._lock:
            self._value += amount
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0

    def __repr__(self) -> str:
        return f"<Counter {self.name}={self.value}>"


class RenderStats:
    """Per-format report rendering totals: renders, failures, bytes, milliseconds.

    Keys are created on first use (pdf, xlsx, ...), so a new format needs no
    registration. Snapshot keys read report_render_<fmt>_<stat>.
    """

    _STATS = ("ok", "failed", "timeout", "too_large", "bytes", "ms")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_fmt: Dict[str, Dict[str, int]] = {}

    def record(self, fmt: str, outcome: str, *, ms: float = 0.0, size: int = 0) -> None:
        with self._lock:
            row = self._by_fmt.setdefault(fmt, {k: 0 for k in self._STATS})
            row[outcome] = row.get(outcome, 0) + 1
            row["ms"] += int(ms)
            row["bytes"] += int(size)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {f"report_render_{fmt}_{k}": v for fmt, row in self._by_fmt.items() for k, v in row.items()}

    def reset(self) -> None:
        with self._lock:
            self._by_fmt.clear()


class BrainMetrics:
    """Operational metrics registry for pipeline observability."""

    def __init__(self):
        self.router_transient_failures = Counter(
            "router_transient_failures",
            "Transient exceptions during intent/endpoint routing"
        )
        self.sql_fallback_entered = Counter(
            "sql_fallback_entered",
            "Queries falling back to local PostgreSQL NL-to-SQL engine"
        )
        self.api_call_failed = Counter(
            "api_call_failed",
            "Accutax REST API calls that failed or returned error status"
        )
        self.fast_router_hits = Counter(
            "fast_router_hits",
            "Queries successfully resolved via deterministic regex fast router"
        )
        self.llm_router_calls = Counter(
            "llm_router_calls",
            "Queries routed via LLM (Gemini Flash)"
        )
        # App Guidance / FAQ (LEFT-path types 1 & 2) coverage against
        # knowledge/accutax_guide.md — see guide_loader.guide_coverage_for.
        # "verified" = matched a real (non-TODO) section, "stub" = matched a
        # section that's still a TODO placeholder, "no_match" = no section
        # heading overlaps the query, "guide_missing" = the file itself
        # failed to load. Watch stub/no_match/guide_missing for content gaps.
        self.guide_coverage_verified = Counter(
            "guide_coverage_verified",
            "App Guidance queries matched to a verified (non-TODO) guide section"
        )
        self.guide_coverage_stub = Counter(
            "guide_coverage_stub",
            "App Guidance queries matched only to a TODO-stub guide section"
        )
        self.guide_coverage_no_match = Counter(
            "guide_coverage_no_match",
            "App Guidance queries with no matching guide section heading"
        )
        self.guide_coverage_missing = Counter(
            "guide_coverage_missing",
            "App Guidance queries where accutax_guide.md itself failed to load"
        )
        # Report integrity (artifacts/integrity.py). verified/checked is the
        # narrative grounding pass rate; sentences_removed and checks_failed
        # should stay near zero — a rise means the narrator or the report
        # builder is drifting from the data.
        self.report_numbers_checked = Counter(
            "report_numbers_checked",
            "Figures found in report narratives and checked against the data"
        )
        self.report_numbers_verified = Counter(
            "report_numbers_verified",
            "Report narrative figures that matched a computed fact"
        )
        self.report_sentences_removed = Counter(
            "report_sentences_removed",
            "Report narrative sentences removed for an unverifiable figure"
        )
        self.report_checks_failed = Counter(
            "report_checks_failed",
            "Report reconciliation checks that failed (totals, identities, row counts)"
        )
        self.report_renders = RenderStats()
        # Convenience alias as noted in spec
        self.router_transient = self.router_transient_failures

    def snapshot(self) -> Dict[str, int]:
        """Return a snapshot dictionary of all metric counters."""
        return {
            "router_transient_failures": self.router_transient_failures.value,
            "sql_fallback_entered": self.sql_fallback_entered.value,
            "api_call_failed": self.api_call_failed.value,
            "fast_router_hits": self.fast_router_hits.value,
            "llm_router_calls": self.llm_router_calls.value,
            "guide_coverage_verified": self.guide_coverage_verified.value,
            "guide_coverage_stub": self.guide_coverage_stub.value,
            "guide_coverage_no_match": self.guide_coverage_no_match.value,
            "guide_coverage_missing": self.guide_coverage_missing.value,
            "report_numbers_checked": self.report_numbers_checked.value,
            "report_numbers_verified": self.report_numbers_verified.value,
            "report_sentences_removed": self.report_sentences_removed.value,
            "report_checks_failed": self.report_checks_failed.value,
            **self.report_renders.snapshot(),
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.router_transient_failures.reset()
        self.sql_fallback_entered.reset()
        self.api_call_failed.reset()
        self.fast_router_hits.reset()
        self.llm_router_calls.reset()
        self.guide_coverage_verified.reset()
        self.guide_coverage_stub.reset()
        self.guide_coverage_no_match.reset()
        self.guide_coverage_missing.reset()
        self.report_numbers_checked.reset()
        self.report_numbers_verified.reset()
        self.report_sentences_removed.reset()
        self.report_checks_failed.reset()
        self.report_renders.reset()


# Global singleton instance
METRICS = BrainMetrics()
