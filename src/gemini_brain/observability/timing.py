"""
timing.py — Structured per-stage latency tracking and query tracing.
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gemini_brain.observability.timing")


@dataclass
class StageTiming:
    """Represents the execution timing and metadata for a single pipeline stage."""
    stage: str
    duration_ms: float
    meta: Dict[str, Any] = field(default_factory=dict)


class QueryTrace:
    """Collects per-stage timings and metadata for one query execution lifecycle."""

    def __init__(self, query_id: Optional[str] = None, org_id: Optional[int] = None):
        self.query_id: str = query_id or str(uuid.uuid4())
        self.org_id: Optional[int] = org_id
        self.stages: List[StageTiming] = []
        self.t0: float = time.perf_counter()
        self._emitted: bool = False

    def set_org_id(self, org_id: int) -> None:
        """Set or update the organization ID once resolved."""
        self.org_id = org_id

    @contextmanager
    def stage(self, name: str, **meta: Any):
        """Context manager to measure and record execution time of a pipeline stage."""
        t = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - t) * 1000.0
            self.stages.append(StageTiming(stage=name, duration_ms=round(duration_ms, 2), meta=meta))

    @property
    def total_ms(self) -> float:
        """Calculate total elapsed time in milliseconds from trace creation."""
        return (time.perf_counter() - self.t0) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert trace to structured dictionary."""
        return {
            "query_id": self.query_id,
            "org_id": self.org_id,
            "total_ms": round(self.total_ms, 2),
            "stages": [asdict(s) for s in self.stages],
        }

    def emit(self) -> Dict[str, Any]:
        """Emit structured log line with all per-stage metrics."""
        summary = self.to_dict()
        logger.info("query_trace: total_ms=%.2f org_id=%s stages=%d", summary["total_ms"], self.org_id, len(self.stages), extra=summary)
        self._emitted = True
        return summary
