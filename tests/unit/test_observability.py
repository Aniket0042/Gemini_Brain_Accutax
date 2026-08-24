"""
test_observability.py — Unit tests for timing stages, query tracing, and metrics counters.
"""
import time
from gemini_brain.observability.metrics import BrainMetrics, Counter, METRICS
from gemini_brain.observability.timing import QueryTrace, StageTiming


def test_stage_timing_dataclass():
    st = StageTiming(stage="classification", duration_ms=123.45, meta={"model": "gemini-2.5-flash"})
    assert st.stage == "classification"
    assert st.duration_ms == 123.45
    assert st.meta["model"] == "gemini-2.5-flash"


def test_query_trace_collection():
    trace = QueryTrace(query_id="test-query-123", org_id=44)
    assert trace.query_id == "test-query-123"
    assert trace.org_id == 44

    with trace.stage("test_stage_1", key="val1"):
        time.sleep(0.01)

    with trace.stage("test_stage_2", key="val2"):
        time.sleep(0.01)

    assert len(trace.stages) == 2
    assert trace.stages[0].stage == "test_stage_1"
    assert trace.stages[0].duration_ms > 0
    assert trace.stages[0].meta["key"] == "val1"
    assert trace.stages[1].stage == "test_stage_2"

    summary = trace.emit()
    assert summary["query_id"] == "test-query-123"
    assert summary["org_id"] == 44
    assert summary["total_ms"] > 0
    assert len(summary["stages"]) == 2


def test_counter_thread_safety_and_reset():
    counter = Counter("test_counter")
    assert counter.value == 0
    counter.inc()
    counter.inc(5)
    assert counter.value == 6
    counter.reset()
    assert counter.value == 0


def test_brain_metrics_registry():
    metrics = BrainMetrics()
    assert metrics.router_transient_failures.value == 0
    assert metrics.sql_fallback_entered.value == 0
    assert metrics.api_call_failed.value == 0

    metrics.router_transient_failures.inc()
    metrics.sql_fallback_entered.inc(2)
    metrics.api_call_failed.inc(3)

    snap = metrics.snapshot()
    assert snap["router_transient_failures"] == 1
    assert snap["sql_fallback_entered"] == 2
    assert snap["api_call_failed"] == 3

    metrics.reset()
    assert metrics.snapshot()["sql_fallback_entered"] == 0
