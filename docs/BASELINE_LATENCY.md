# Gemini Brain — Baseline Latency Report (Phase 0)

> **Date:** 2026-08-17 18:32:28 UTC  
> **Environment:** Python 3.12, Google Gemini 2.5 Flash, AWS Bedrock Claude, PostgreSQL Accutax  
> **Purpose:** Baseline stage-by-stage measurement of the 14 representative PRD queries before applying latency refactoring.

---

## 1. Executive Summary & Macro Numbers

| Query Execution Category | Count | p50 Latency (s) | p95 Latency (s) | Min (s) | Max (s) |
|---|---|---|---|---|---|
| **Overall (All 14 Queries)** | 14 | **6.35s** | **22.74s** | 4.25s | 79.32s |
| **Left Path (Gemini Direct / FAQ)** | 0 | **0.00s** | **0.00s** | 0.00s | 0.00s |
| **Right Path (API → Claude Reasoner)** | 0 | **0.00s** | **0.00s** | 0.00s | 0.00s |
| **Fallback Path (NL-to-SQL DB Engine)** | 14 | **6.35s** | **22.74s** | 4.25s | 79.32s |

---

## 2. Stage-by-Stage Latency Breakdown

The table below records the execution duration across all pipeline stages captured via `QueryTrace`:

| Pipeline Stage | Invocation Count | p50 (ms) | p95 (ms) | Avg (ms) | Min (ms) | Max (ms) | Target in Phase 1-3 |
|---|---|---|---|---|---|---|---|
| `api_call` | 4 | **70.7ms** | **74.4ms** | 72.7ms | 61.7ms | 83.9ms | Async httpx (< 150ms) |
| `classification` | 14 | **217.2ms** | **231.0ms** | 922.4ms | 155.5ms | 10153.0ms | Bypass in Phase 2 for fast queries (< 10ms) |
| `endpoint_selection` | 14 | **169.2ms** | **199.6ms** | 174.1ms | 151.6ms | 207.2ms | Prefix cached (< 800ms) / Tool Router |
| `pii_redaction` | 14 | **21.2ms** | **43.6ms** | 230.9ms | 14.7ms | 2887.2ms | < 100ms |
| `sql_fallback` | 14 | **5872.3ms** | **22335.1ms** | 12886.9ms | 3759.4ms | 78924.9ms | < 100ms |
| `tenant_isolation` | 14 | **0.0ms** | **0.0ms** | 0.0ms | 0.0ms | 0.0ms | < 100ms |

---

## 3. Operational Counters & Failure Diagnostics

| Metric Counter | Recorded Count | Root Cause / Context |
|---|---|---|
| `sql_fallback_entered` | **14** | Entered when endpoint selection returns no match or API is missing. |
| `router_transient_failures` | **14** | Unhandled exceptions in `endpoint_selector.py` caught and escalated. |
| `api_call_failed` | **4** | Backend REST API calls returning 4xx/5xx or timeout. |

---

## 4. Query-by-Query Detailed Log

| ID | Category | Query Text | Path | Total Duration | Key Stages (ms) |
|---|---|---|---|---|---|
| **Q01** | FAQ / How-to | How do I create a recurring invoice in Accutax? | `db_fallback` | **20.29s** | pii_redaction: 2887ms, tenant_isolation: 0ms, classification: 10153ms, endpoint_selection: 169ms |
| **Q02** | App Guidance | Where can I view bank reconciliation? | `db_fallback` | **4.73s** | pii_redaction: 38ms, tenant_isolation: 0ms, classification: 205ms, endpoint_selection: 160ms |
| **Q03** | App Guidance | Where do I record a journal entry? | `db_fallback` | **5.51s** | pii_redaction: 33ms, tenant_isolation: 0ms, classification: 223ms, endpoint_selection: 180ms |
| **Q04** | Accounting Concept | What is accounts receivable aging? | `db_fallback` | **4.91s** | pii_redaction: 15ms, tenant_isolation: 0ms, classification: 229ms, endpoint_selection: 174ms |
| **Q05** | Strategic Advice | Give me a business health check summary and recommendations. | `db_fallback` | **22.74s** | pii_redaction: 16ms, tenant_isolation: 0ms, classification: 231ms, endpoint_selection: 161ms |
| **Q06** | Data Lookup | What is our total revenue this year? | `db_fallback` | **6.35s** | pii_redaction: 34ms, tenant_isolation: 0ms, classification: 216ms, endpoint_selection: 163ms |
| **Q07** | Data Lookup | How much total expenses do we have this year? | `db_fallback` | **4.25s** | pii_redaction: 21ms, tenant_isolation: 0ms, classification: 197ms, endpoint_selection: 200ms |
| **Q08** | Report | Show me the Profit and Loss statement for this year | `db_fallback` | **12.38s** | pii_redaction: 21ms, tenant_isolation: 0ms, classification: 155ms, endpoint_selection: 159ms |
| **Q09** | Report | Show Balance Sheet as of today | `db_fallback` | **12.41s** | pii_redaction: 16ms, tenant_isolation: 0ms, classification: 224ms, endpoint_selection: 152ms |
| **Q10** | Data Lookup | Show all uncategorized bank transactions. | `db_fallback` | **4.91s** | pii_redaction: 20ms, tenant_isolation: 0ms, classification: 217ms, endpoint_selection: 187ms |
| **Q11** | Data Lookup | What are my top unpaid customer invoices? | `db_fallback` | **6.41s** | pii_redaction: 39ms, tenant_isolation: 0ms, classification: 217ms, endpoint_selection: 187ms |
| **Q12** | Forecast | Show expected cash flow projection for next month | `db_fallback` | **6.10s** | pii_redaction: 30ms, tenant_isolation: 0ms, classification: 200ms, endpoint_selection: 207ms |
| **Q13** | Complex Multi-Source | Analyze expense growth vs income over the last 6 months. | `db_fallback` | **8.98s** | pii_redaction: 44ms, tenant_isolation: 0ms, classification: 228ms, endpoint_selection: 180ms |
| **Q14** | Audit / Special | Show recent audit log activities for user deletions and sensitive changes. | `db_fallback` | **79.32s** | pii_redaction: 19ms, tenant_isolation: 0ms, classification: 218ms, endpoint_selection: 160ms |

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
