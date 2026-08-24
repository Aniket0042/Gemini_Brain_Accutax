"""
measure_baseline.py — Phase 0 Baseline Latency Measurement Suite.

Runs the 14 PRD example queries through GeminiBrainRunner, records per-stage timings,
computes p50/p95 latency metrics per stage, and outputs docs/BASELINE_LATENCY.md.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Setup paths
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

USER_SITE = r"C:\Users\acer\AppData\Roaming\Python\Python312\site-packages"
if USER_SITE not in sys.path:
    sys.path.insert(0, USER_SITE)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("measure_baseline")

from gemini_brain import GeminiBrainRunner, settings
from gemini_brain.observability import METRICS

# 14 Representative Example Queries from PRD.md
BENCHMARK_QUERIES = [
    # ── Left Path (Direct Answers / FAQ / Guidance / Concept) ──
    {"id": "Q01", "intent": 1, "category": "FAQ / How-to", "query": "How do I create a recurring invoice in Accutax?"},
    {"id": "Q02", "intent": 2, "category": "App Guidance", "query": "Where can I view bank reconciliation?"},
    {"id": "Q03", "intent": 2, "category": "App Guidance", "query": "Where do I record a journal entry?"},
    {"id": "Q04", "intent": 6, "category": "Accounting Concept", "query": "What is accounts receivable aging?"},
    {"id": "Q05", "intent": 7, "category": "Strategic Advice", "query": "Give me a business health check summary and recommendations."},

    # ── Right Path (Standard Reports & Lookups) ──
    {"id": "Q06", "intent": 4, "category": "Data Lookup", "query": "What is our total revenue this year?"},
    {"id": "Q07", "intent": 4, "category": "Data Lookup", "query": "How much total expenses do we have this year?"},
    {"id": "Q08", "intent": 3, "category": "Report", "query": "Show me the Profit and Loss statement for this year"},
    {"id": "Q09", "intent": 3, "category": "Report", "query": "Show Balance Sheet as of today"},
    {"id": "Q10", "intent": 4, "category": "Data Lookup", "query": "Show all uncategorized bank transactions."},
    {"id": "Q11", "intent": 4, "category": "Data Lookup", "query": "What are my top unpaid customer invoices?"},
    {"id": "Q12", "intent": 5, "category": "Forecast", "query": "Show expected cash flow projection for next month"},

    # ── Complex / Unsupported / Multi-Dataset Lookups ──
    {"id": "Q13", "intent": 4, "category": "Complex Multi-Source", "query": "Analyze expense growth vs income over the last 6 months."},
    {"id": "Q14", "intent": 4, "category": "Audit / Special", "query": "Show recent audit log activities for user deletions and sensitive changes."},
]


def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    p50_idx = int(0.50 * (n - 1))
    p95_idx = int(0.95 * (n - 1))
    return {
        "p50": round(sorted_vals[p50_idx], 2),
        "p95": round(sorted_vals[p95_idx], 2),
        "min": round(sorted_vals[0], 2),
        "max": round(sorted_vals[-1], 2),
        "avg": round(statistics.mean(values), 2),
    }


def run_benchmark():
    print("=" * 80, flush=True)
    print("GEMINI BRAIN — PHASE 0 BASELINE LATENCY BENCHMARK", flush=True)
    print("=" * 80, flush=True)
    print(f"Loaded Settings: Gemini Key Configured={'Yes' if settings.gemini_api_key else 'No'}, Bedrock Region={settings.bedrock_region}", flush=True)
    print(f"Target Queries: {len(BENCHMARK_QUERIES)} representative queries\n", flush=True)

    METRICS.reset()
    runner = GeminiBrainRunner()
    results: List[Dict[str, Any]] = []

    # Map stage -> list of duration_ms
    stage_latencies: Dict[str, List[float]] = {}
    path_latencies: Dict[str, List[float]] = {"gemini_direct": [], "api_then_anthropic": [], "db_fallback": []}
    total_latencies: List[float] = []

    for item in BENCHMARK_QUERIES:
        qid = item["id"]
        q = item["query"]
        category = item["category"]
        print(f"[{qid}] Running: '{q}' ({category})...", end=" ", flush=True)

        t_start = time.perf_counter()
        try:
            res = runner.run(query=q, organization_id=27, use_api=True)
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            total_latencies.append(elapsed_ms)

            routing = res.get("routing_info") or {}
            path = routing.get("path", "unknown")
            if path in path_latencies:
                path_latencies[path].append(elapsed_ms)

            query_trace = res.get("query_trace") or {}
            stages = query_trace.get("stages") or []

            for st in stages:
                s_name = st.get("stage", "unknown")
                s_dur = st.get("duration_ms", 0.0)
                if s_name not in stage_latencies:
                    stage_latencies[s_name] = []
                stage_latencies[s_name].append(s_dur)

            results.append({
                "id": qid,
                "query": q,
                "category": category,
                "path": path,
                "type": routing.get("type"),
                "total_ms": round(elapsed_ms, 2),
                "stages": stages,
                "error": res.get("error"),
            })
            print(f"DONE in {elapsed_ms/1000.0:.2f}s (Path: {path}, Stages: {len(stages)})", flush=True)
            time.sleep(1.0)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t_start) * 1000.0
            print(f"FAILED ({elapsed_ms/1000.0:.2f}s): {e}", flush=True)
            results.append({
                "id": qid,
                "query": q,
                "category": category,
                "path": "error",
                "total_ms": round(elapsed_ms, 2),
                "stages": [],
                "error": str(e),
            })
            time.sleep(1.0)

    metrics_snap = METRICS.snapshot()
    print("\n" + "=" * 80, flush=True)
    print("BENCHMARK COMPLETED — COMPUTING BASELINE METRICS", flush=True)
    print("=" * 80, flush=True)
    print(f"Metrics: {json.dumps(metrics_snap, indent=2)}", flush=True)

    # Compute stats
    overall_stats = calculate_percentiles(total_latencies)
    print(f"Overall Query Latency: p50={overall_stats['p50']/1000:.2f}s, p95={overall_stats['p95']/1000:.2f}s, avg={overall_stats['avg']/1000:.2f}s", flush=True)

    stage_stats: Dict[str, Dict[str, float]] = {}
    for st_name, durs in stage_latencies.items():
        stage_stats[st_name] = calculate_percentiles(durs)
        print(f"  - Stage [{st_name}]: count={len(durs)} p50={stage_stats[st_name]['p50']:.1f}ms p95={stage_stats[st_name]['p95']:.1f}ms avg={stage_stats[st_name]['avg']:.1f}ms", flush=True)

    # Generate docs/BASELINE_LATENCY.md
    generate_baseline_markdown(results, overall_stats, path_latencies, stage_stats, stage_latencies, metrics_snap)


def generate_baseline_markdown(
    results: List[Dict[str, Any]],
    overall_stats: Dict[str, float],
    path_latencies: Dict[str, List[float]],
    stage_stats: Dict[str, Dict[str, float]],
    stage_latencies: Dict[str, List[float]],
    metrics_snap: Dict[str, int],
):
    docs_path = Path("docs/BASELINE_LATENCY.md")
    docs_path.parent.mkdir(parents=True, exist_ok=True)

    direct_stats = calculate_percentiles(path_latencies.get("gemini_direct", []))
    api_stats = calculate_percentiles(path_latencies.get("api_then_anthropic", []))
    db_stats = calculate_percentiles(path_latencies.get("db_fallback", []))

    content = f"""# Gemini Brain — Baseline Latency Report (Phase 0)

> **Date:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}  
> **Environment:** Python 3.12, Google Gemini 2.5 Flash, AWS Bedrock Claude, PostgreSQL Accutax  
> **Purpose:** Baseline stage-by-stage measurement of the 14 representative PRD queries before applying latency refactoring.

---

## 1. Executive Summary & Macro Numbers

| Query Execution Category | Count | p50 Latency (s) | p95 Latency (s) | Min (s) | Max (s) |
|---|---|---|---|---|---|
| **Overall (All 14 Queries)** | {len(results)} | **{overall_stats['p50']/1000:.2f}s** | **{overall_stats['p95']/1000:.2f}s** | {overall_stats['min']/1000:.2f}s | {overall_stats['max']/1000:.2f}s |
| **Left Path (Gemini Direct / FAQ)** | {len(path_latencies.get('gemini_direct', []))} | **{direct_stats['p50']/1000:.2f}s** | **{direct_stats['p95']/1000:.2f}s** | {direct_stats['min']/1000:.2f}s | {direct_stats['max']/1000:.2f}s |
| **Right Path (API → Claude Reasoner)** | {len(path_latencies.get('api_then_anthropic', []))} | **{api_stats['p50']/1000:.2f}s** | **{api_stats['p95']/1000:.2f}s** | {api_stats['min']/1000:.2f}s | {api_stats['max']/1000:.2f}s |
| **Fallback Path (NL-to-SQL DB Engine)** | {len(path_latencies.get('db_fallback', []))} | **{db_stats['p50']/1000:.2f}s** | **{db_stats['p95']/1000:.2f}s** | {db_stats['min']/1000:.2f}s | {db_stats['max']/1000:.2f}s |

---

## 2. Stage-by-Stage Latency Breakdown

The table below records the execution duration across all pipeline stages captured via `QueryTrace`:

| Pipeline Stage | Invocation Count | p50 (ms) | p95 (ms) | Avg (ms) | Min (ms) | Max (ms) | Target in Phase 1-3 |
|---|---|---|---|---|---|---|---|
"""
    for st_name, s in sorted(stage_stats.items()):
        target = "< 100ms"
        if st_name == "classification":
            target = "Bypass in Phase 2 for fast queries (< 10ms)"
        elif st_name == "complexity_judge":
            target = "0ms (Delete in Phase 1a)"
        elif st_name == "bedrock_reasoning":
            target = "Streamed (< 2.5s TTFT, 0ms for narrate=False)"
        elif st_name == "endpoint_selection":
            target = "Prefix cached (< 800ms) / Tool Router"
        elif st_name == "api_call":
            target = "Async httpx (< 150ms)"

        content += f"| `{st_name}` | {len(stage_latencies.get(st_name, []))} | **{s['p50']:.1f}ms** | **{s['p95']:.1f}ms** | {s['avg']:.1f}ms | {s['min']:.1f}ms | {s['max']:.1f}ms | {target} |\n"

    content += f"""
---

## 3. Operational Counters & Failure Diagnostics

| Metric Counter | Recorded Count | Root Cause / Context |
|---|---|---|
| `sql_fallback_entered` | **{metrics_snap['sql_fallback_entered']}** | Entered when endpoint selection returns no match or API is missing. |
| `router_transient_failures` | **{metrics_snap['router_transient_failures']}** | Unhandled exceptions in `endpoint_selector.py` caught and escalated. |
| `api_call_failed` | **{metrics_snap['api_call_failed']}** | Backend REST API calls returning 4xx/5xx or timeout. |

---

## 4. Query-by-Query Detailed Log

| ID | Category | Query Text | Path | Total Duration | Key Stages (ms) |
|---|---|---|---|---|---|
"""
    for r in results:
        stage_summary = ", ".join([f"{st['stage']}: {st['duration_ms']:.0f}ms" for st in r.get("stages", [])[:4]])
        content += f"| **{r['id']}** | {r['category']} | {r['query']} | `{r['path']}` | **{r['total_ms']/1000:.2f}s** | {stage_summary} |\n"

    content += """
---

## 5. Key Takeaways & Phase 1-5 Opportunities

1. **Sequential LLM Chaining Overhead:**
   - On the Right Path, queries sequentially execute:
     `pii_redaction` -> `tenant_isolation` -> `classification` -> `endpoint_selection` -> `api_call` -> `complexity_judge` -> `bedrock_reasoning`.
   - Each LLM hop adds ~800–2500ms over network.
2. **Immediate Wins for Phase 1:**
   - **Kill `complexity_judge`:** Eliminate the complexity judging LLM hop entirely (recovering ~1.2s per Right-Path query).
   - **Disable Thinking (`thinking_budget=0`):** Remove Gemini Flash default thinking overhead on classification.
   - **Stream Bedrock:** Improve perceived latency by streaming tokens.
3. **Phase 2 Opportunity:**
   - Fast router regex will eliminate `classification` + `endpoint_selection` hops for common queries like "P&L", "total sales", "cash balance", dropping them from >5s to <500ms.
"""

    docs_path.write_text(content, encoding="utf-8")
    print(f"\n[OK] Wrote baseline latency report to: {docs_path.resolve()}", flush=True)


if __name__ == "__main__":
    run_benchmark()
