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
        }

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.router_transient_failures.reset()
        self.sql_fallback_entered.reset()
        self.api_call_failed.reset()
        self.fast_router_hits.reset()
        self.llm_router_calls.reset()


# Global singleton instance
METRICS = BrainMetrics()
