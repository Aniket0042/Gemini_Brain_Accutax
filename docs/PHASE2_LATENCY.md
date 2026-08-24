# Phase 2 Fast Router & Latency Benchmark Report

Generated automatically by `scripts/measure_phase2.py`.

## 1. Fast Router Performance Summary

| Metric | Target | Phase 2 Achieved | Status |
|---|---|---|---|
| **Fast Router Hit Rate** | > 40.0% | **57.1% (8/14)** | **PASSED** |
| **Gemini LLM Calls on Fast Route Hit** | 0 | **0 (Zero Gemini calls)** | **PASSED** |
| **Timezone-Aware Date Resolution** | Asia/Dubai (UTC+4) | **Verified in `router/dates.py`** | **PASSED** |

## 2. PRD Query Route Classification Breakdown

| ID | Category | Query | Router Source | Target Endpoint / Action |
|---|---|---|---|---|
| **Q01** | FAQ / How-to | `How do I create a recurring invoice in Accutax?` | LLM (Gemini Flash) | Conversational / Fallback |
| **Q02** | App Guidance | `Where can I view bank reconciliation?` | LLM (Gemini Flash) | Conversational / Fallback |
| **Q03** | App Guidance | `Where do I record a journal entry?` | LLM (Gemini Flash) | Conversational / Fallback |
| **Q04** | Accounting Concept | `What is accounts receivable aging?` | **FAST** (`customer_balance_summary`) | `/report/customer-balance-summary` |
| **Q05** | Strategic Advice | `Give me a business health check summary and recommendations.` | **FAST** (`dashboard_overview`) | `/report/profit-loss` |
| **Q06** | Data Lookup | `What is our total revenue this year?` | **FAST** (`income_total`) | `/income/total` |
| **Q07** | Data Lookup | `How much total expenses do we have this year?` | **FAST** (`expense_total`) | `/expense/total` |
| **Q08** | Report | `Show me the Profit and Loss statement for this year` | **FAST** (`profit_loss`) | `/report/profit-loss` |
| **Q09** | Report | `Show Balance Sheet as of today` | **FAST** (`balance_sheet`) | `/report/balance-sheet` |
| **Q10** | Data Lookup | `Show all uncategorized bank transactions.` | **FAST** (`uncategorized_transactions`) | `/bank/manual/unassigned-transactions` |
| **Q11** | Data Lookup | `What are my top unpaid customer invoices?` | LLM (Gemini Flash) | Conversational / Fallback |
| **Q12** | Forecast | `Show expected cash flow projection for next month` | **FAST** (`cash_forecast`) | `/report/cash-forecast` |
| **Q13** | Complex Multi-Source | `Analyze expense growth vs income over the last 6 months.` | LLM (Gemini Flash) | Conversational / Fallback |
| **Q14** | Audit / Special | `Show recent audit log activities for user deletions and sensitive changes.` | LLM (Gemini Flash) | Conversational / Fallback |
