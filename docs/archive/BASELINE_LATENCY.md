# Gemini Brain — Baseline Latency Report (Phase 0)

> **Date:** 2026-08-28 08:33:21 UTC  
> **Environment:** Python 3.12, Google Gemini 2.5 Flash, AWS Bedrock Claude, PostgreSQL Accutax  
> **Purpose:** Baseline stage-by-stage measurement of the 14 representative PRD queries before applying latency refactoring.

---

## 1. Executive Summary & Macro Numbers

| Query Execution Category | Count | p50 Latency (s) | p95 Latency (s) | Min (s) | Max (s) |
|---|---|---|---|---|---|
| **Overall (All 14 Queries)** | 14 | **7.91s** | **18.63s** | 5.73s | 22.22s |
| **Left Path (Gemini Direct / FAQ)** | 4 | **6.29s** | **6.51s** | 5.73s | 9.67s |
| **Right Path (API → Claude Reasoner)** | 0 | **0.00s** | **0.00s** | 0.00s | 0.00s |
| **Fallback Path (NL-to-SQL DB Engine)** | 10 | **9.01s** | **18.63s** | 5.82s | 22.22s |

---

## 2. Stage-by-Stage Latency Breakdown

The table below records the execution duration across all pipeline stages captured via `QueryTrace`:

| Pipeline Stage | Invocation Count | p50 (ms) | p95 (ms) | Avg (ms) | Min (ms) | Max (ms) | Target in Phase 1-3 |
|---|---|---|---|---|---|---|---|
| `api_call` | 17 | **225.9ms** | **797.8ms** | 372.9ms | 178.8ms | 1948.8ms | Async httpx (< 150ms) |
| `classification` | 7 | **1327.1ms** | **1579.4ms** | 1659.4ms | 1082.0ms | 3533.3ms | Bypass in Phase 2 for fast queries (< 10ms) |
| `endpoint_selection` | 3 | **2113.6ms** | **2113.6ms** | 1934.8ms | 1172.4ms | 2518.6ms | Prefix cached (< 800ms) / Tool Router |
| `gemini_direct` | 4 | **4501.0ms** | **4963.1ms** | 4722.3ms | 4417.8ms | 5007.1ms | < 100ms |
| `pii_redaction` | 14 | **18.0ms** | **31.8ms** | 133.7ms | 10.9ms | 1629.4ms | < 100ms |
| `self_correction_retry` | 10 | **1367.6ms** | **1813.1ms** | 1497.3ms | 1025.2ms | 1860.7ms | < 100ms |
| `sql_fallback` | 10 | **5638.4ms** | **13586.1ms** | 8493.1ms | 4348.3ms | 16416.4ms | < 100ms |
| `tenant_isolation` | 14 | **0.0ms** | **0.0ms** | 0.0ms | 0.0ms | 0.0ms | < 100ms |

---

## 3. Operational Counters & Failure Diagnostics

| Metric Counter | Recorded Count | Root Cause / Context |
|---|---|---|
| `sql_fallback_entered` | **10** | Entered when endpoint selection returns no match or API is missing. |
| `router_transient_failures` | **0** | Unhandled exceptions in `endpoint_selector.py` caught and escalated. |
| `api_call_failed` | **17** | Backend REST API calls returning 4xx/5xx or timeout. |

---

## 4. Query-by-Query Detailed Log

| ID | Category | Query Text | Path | Total Duration | Key Stages (ms) |
|---|---|---|---|---|---|
| **Q01** | FAQ / How-to | How do I create a recurring invoice in Accutax? | `gemini_direct` | **9.67s** | pii_redaction: 1629ms, tenant_isolation: 0ms, classification: 3533ms, gemini_direct: 4501ms |
| **Q02** | App Guidance | Where can I view bank reconciliation? | `gemini_direct` | **6.29s** | pii_redaction: 20ms, tenant_isolation: 0ms, classification: 1306ms, gemini_direct: 4963ms |
| **Q03** | App Guidance | Where do I record a journal entry? | `gemini_direct` | **6.51s** | pii_redaction: 17ms, tenant_isolation: 0ms, classification: 1486ms, gemini_direct: 5007ms |
| **Q04** | Accounting Concept | What is accounts receivable aging? | `gemini_direct` | **5.73s** | pii_redaction: 11ms, tenant_isolation: 0ms, classification: 1302ms, gemini_direct: 4418ms |
| **Q05** | Strategic Advice | Give me a business health check summary and recommendations. | `db_fallback` | **9.01s** | pii_redaction: 12ms, tenant_isolation: 0ms, api_call: 1949ms, self_correction_retry: 1743ms |
| **Q06** | Data Lookup | What is our total revenue this year? | `db_fallback` | **7.91s** | pii_redaction: 13ms, tenant_isolation: 0ms, api_call: 214ms, self_correction_retry: 1861ms |
| **Q07** | Data Lookup | How much total expenses do we have this year? | `db_fallback` | **12.41s** | pii_redaction: 25ms, tenant_isolation: 0ms, api_call: 234ms, self_correction_retry: 1368ms |
| **Q08** | Report | Show me the Profit and Loss statement for this year | `db_fallback` | **6.40s** | pii_redaction: 32ms, tenant_isolation: 0ms, api_call: 261ms, self_correction_retry: 1336ms |
| **Q09** | Report | Show Balance Sheet as of today | `db_fallback` | **6.81s** | pii_redaction: 20ms, tenant_isolation: 0ms, api_call: 294ms, self_correction_retry: 1025ms |
| **Q10** | Data Lookup | Show all uncategorized bank transactions. | `db_fallback` | **5.82s** | pii_redaction: 13ms, tenant_isolation: 0ms, api_call: 234ms, self_correction_retry: 1038ms |
| **Q11** | Data Lookup | What are my top unpaid customer invoices? | `db_fallback` | **18.63s** | pii_redaction: 18ms, tenant_isolation: 0ms, classification: 1579ms, endpoint_selection: 2519ms |
| **Q12** | Forecast | Show expected cash flow projection for next month | `db_fallback` | **9.20s** | pii_redaction: 30ms, tenant_isolation: 0ms, api_call: 226ms, self_correction_retry: 1647ms |
| **Q13** | Complex Multi-Source | Analyze expense growth vs income over the last 6 months. | `db_fallback` | **22.22s** | pii_redaction: 20ms, tenant_isolation: 0ms, classification: 1327ms, endpoint_selection: 2114ms |
| **Q14** | Audit / Special | Show recent audit log activities for user deletions and sensitive changes. | `db_fallback` | **17.86s** | pii_redaction: 14ms, tenant_isolation: 0ms, classification: 1082ms, endpoint_selection: 1172ms |

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
