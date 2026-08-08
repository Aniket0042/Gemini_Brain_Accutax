# Product Requirement Document (PRD)
## Project: Gemini Brain — AI Orchestration Engine for Accutax Bookkeeping Platform

---

## 1. Executive Summary & Product Vision

### 1.1 Product Vision
**Gemini Brain** is the intelligent, context-aware AI subsystem embedded within **Accutax**—a cloud-based bookkeeping and financial management software platform designed for SMEs, accountants, and financial advisors in the GCC region (UAE/Middle East context with 5% VAT and AED currency).

Traditional accounting software requires users to manually navigate complex menus, build custom financial reports, and extract rows of ledger data. **Gemini Brain** converts Accutax into a **Conversational Financial Intelligence Platform**, enabling users to ask natural language questions ("What is our total net profit this year after VAT?", "Where do I add a recurring vendor bill?", "Analyze our cash flow risk for Q3") and receive instant, verified answers backed by live accounting data.

### 1.2 Core Architectural Strategy
Gemini Brain utilizes a **Hybrid Dual-LLM Orchestration Model**:
1. **Google Gemini 2.5 Flash** acts as the high-speed **Orchestrator**: handles 7-type intent classification, endpoint selection, complexity judging, parameter normalization, and direct conversational Q&A for guidance/FAQ.
2. **Anthropic Claude on AWS Bedrock** acts as the deep **Financial Reasoning Engine**: performs complex data analysis, trend extrapolation, ratio calculation, and strategic financial advice over live Accutax API responses.
3. **Accutax REST API Backend** serves as the **Single Source of Truth**, with an automated **PostgreSQL NL-to-SQL Fallback Engine** when API endpoints are missing or return no data.

---

## 2. Target User Personas & Use Cases

| Persona | Primary Needs | Key Gemini Brain Use Cases |
| :--- | :--- | :--- |
| **SME Owner / Business Manager** | Quick insights on revenue, profit, cash flow, and tax obligations without navigating accounting menus. | - "How much revenue did we make this month?"<br>- "What are my top 5 unpaid customer invoices?"<br>- "Is our cash flow healthy for next quarter?" |
| **Bookkeeper / Accountant** | Quick navigation to UI screens, transaction lookup, general ledger verification, and journal entry checks. | - "Where do I record a journal entry?"<br>- "Show all uncategorized bank transactions."<br>- "List journal entries for organization 5." |
| **CFO / Financial Controller** | Deep financial analysis, audit trail review, expense breakdown, and strategic decision support. | - "Analyze expense growth vs. income over the last 6 months."<br>- "Show recent audit log activities for user deletions."<br>- "Calculate net VAT liability for Q2." |

---

## 3. Product Goals & Key Performance Indicators (KPIs)

| Goal Area | Target KPI | Rationale |
| :--- | :--- | :--- |
| **Response Latency** | $\le 1.2\text{s}$ for Direct Q&A (Left Path)<br>$\le 2.5\text{s}$ for Deep Analytical Data Queries (Right Path) | Ensures real-time interactive user experience without UI blocking. |
| **Routing Accuracy** | $\ge 98.5\%$ Intent Classification Accuracy | Prevents routing data queries to static FAQ models or vice versa. |
| **Data Integrity & Zero Hallucination** | $100\%$ factual grounding on API data | Financial calculations must strictly reflect live database/API payloads. |
| **Tenant Isolation & Security** | $0\%$ cross-tenant data leakage | Multi-tenant organization boundaries enforced dynamically via JWT/session claims. |
| **Inference Cost Optimization** | $65\% - 75\%$ cost reduction vs pure Claude Sonnet | Gemini 2.5 Flash routes ~50% queries locally without invoking expensive reasoning models. |

---

## 4. System Architecture & High-Level Workflow

```
                       ┌───────────────────────┐
                       │     User Question     │
                       └───────────┬───────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │ Intent Classification (1-7) │
                    │   (Google Gemini Flash)     │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
   [LEFT PATH: Types 1, 2, 6, 7]             [RIGHT PATH: Types 3, 4, 5]
 (FAQ, Guidance, Concept, Advice)           (Report, Data Query, Forecast)
              │                                         │
              ▼                                         ▼
     Gemini Direct Answer                     Endpoint Selection (Gemini)
                                                        │
                                                        ▼
                                             Accutax REST API Call
                                                        │
                                       ┌────────────────┴────────────────┐
                                       ▼                                 ▼
                              [Data Retrieved]                    [API Failed/No Endpoint]
                                       │                                 │
                                       ▼                                 ▼
                             Complexity Judging                  DB Fallback Engine
                               (SIMPLE/MED/CMPLX)                   (NL-to-SQL)
                                       │                                 │
                                       ▼                                 ▼
                              Claude on Bedrock                    PostgreSQL DB
```

---

## 5. Detailed Functional Requirements

### 5.1 7-Type Intent Classification Engine
Every incoming natural language query is analyzed by **Google Gemini 2.5 Flash** and categorized into exactly one of 7 intent types:

| Type | Intent Name | Description | Routing Path | Target SLA |
| :---: | :--- | :--- | :---: | :---: |
| **1** | **FAQ / How-to** | Procedural instructions for platform usage (e.g., *"How do I create a recurring invoice?"*) | Left Path | $< 1.0\text{s}$ |
| **2** | **App Guidance** | Navigation assistance within Accutax UI (e.g., *"Where can I view bank reconciliation?"*) | Left Path | $< 1.0\text{s}$ |
| **3** | **Report Generation** | Structured financial statements (e.g., *"Generate P&L report for 2026"*, *"Show Balance Sheet"*) | Right Path | $< 2.5\text{s}$ |
| **4** | **Data Query** | Live lookup of financial data (e.g., *"Total sales this year"*, *"List top unpaid invoices"*) | Right Path | $< 2.0\text{s}$ |
| **5** | **Forecast & Prediction**| Projections & future trend estimations (e.g., *"Forecast Q4 revenue based on current growth"*) | Right Path | $< 2.5\text{s}$ |
| **6** | **Accounting Concept**| Definitions and standards (e.g., *"What is accounts receivable?"*, *"Explain UAE VAT 5% rule"*) | Left Path | $< 1.0\text{s}$ |
| **7** | **Strategic Summary** | Executive business advice (e.g., *"Give me a business health check summary"*) | Left Path / Right | $< 2.0\text{s}$ |

---

### 5.2 Accutax REST API Integration Catalog
When the Right Path (Types 3, 4, 5) is triggered, **Gemini 2.5 Flash** maps the query to the optimal endpoint in the Accutax backend:

- **Invoices / Income**:
  - `GET /income/list`: Paginated invoice records (filters: status, date range, search).
  - `GET /income/total`: Income total & tax sum (`user_id`, `filter_year`, `filter_type`: YEARLY/QUARTERLY/MONTHLY).
  - `GET /income/customer-payment/list`: Customer payment collections.
- **Expenses / Vendor Bills**:
  - `GET /expense/list`: Paginated expense bills.
  - `GET /expense/total`: Expense totals by date range.
  - `GET /expense/supplier-payment/list`: Vendor payment records.
- **Banking**:
  - `GET /bank/manual/accounts`: Bank accounts and live balances.
  - `GET /bank/transactions/uncategorized`: Transactions pending categorization.
- **Contacts**:
  - `GET /contact/list`: Customers (`contact_type_id=4`) or Vendors (`contact_type_id=1,2,3`).
- **Chart of Accounts & General Ledger**:
  - `GET /chart-of-accounts`: Account hierarchy.
  - `GET /accounting/journal-entries`: Journal entry records.
  - `GET /accounting/general-ledger`: Ledger account breakdown.
- **Dashboard & Audit**:
  - `GET /dashboard/web/v3`: Comprehensive monthly revenue, expense, and outstanding financial metrics.
  - `GET /audit-logs`: System audit trail records.

---

### 5.3 Complexity Judging & Model Switching
To optimize performance and token cost, **Gemini 2.5 Flash** evaluates the retrieved data payload and query complexity:
- **SIMPLE**: Straightforward single-metric summaries $\rightarrow$ routed to **Claude 3.5 Haiku on AWS Bedrock**.
- **MEDIUM**: Multi-field filter lookups and simple list formatting $\rightarrow$ routed to **Claude 3.5 Haiku on AWS Bedrock**.
- **COMPLEX**: Strategic ratio analysis, forecasting, multi-period trend comparison, or cross-dataset synthesis $\rightarrow$ routed to **Claude 3.5 Sonnet on AWS Bedrock**.

---

### 5.4 PostgreSQL NL-to-SQL Fallback Engine
If an API endpoint does not exist for a niche query or returns a 404/500 error:
1. The **NL-to-SQL Engine** inspects the PostgreSQL database schema for Accutax (`accutax_bk_1_5`).
2. Gemini 2.5 Flash generates a read-only SQL query filtered strictly by `organization_id`.
3. **SQL Safety Enforcement**:
   - Rejects non-`SELECT` statements (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE` are strictly forbidden).
   - Enforces explicit `WHERE organization_id = :org_id` clauses to maintain tenant isolation.
   - Formats SQL results before passing to Claude for final response synthesis.

---

### 5.5 Multi-Tenant Isolation & PII Redaction

1. **Organization & User Scope**:
   - Every request enforces `organization_id` and `user_id`.
   - The `TenantResolver` dynamically injects organization context into API query parameters and SQL WHERE clauses.
2. **PII & Sensitivity Safeguards**:
   - `redact_pii()` automatically scrubs sensitive PII (credit card numbers, IBANs, personal email addresses) prior to sending prompts to external LLM providers.
   - Credentials (`.env`) are strictly locked and ignored from git version control.

---

### 5.6 Session Memory & Context Extraction
- Tracks multi-turn chat conversations using session UUIDs.
- Automatically generates concise conversation titles after the second turn (e.g., *"Q1 Revenue & Tax Analysis"*).
- Extracts updated state entities (e.g., current active date range, current selected contact/invoice) to allow follow-up questions like *"Now show only unpaid ones"*.

---

## 6. API Interface & Frontend UX Specification

### 6.1 Backend REST API Specs (`FastAPI`)
- `POST /api/v1/query`: Synchronous JSON response including `answer`, `routing_info`, `token_usage`, `cost_usd`, and `elapsed_seconds`.
- `POST /api/v1/query/stream`: Server-Sent Events (SSE) stream emitting status events (`classification`, `retrieval`, `analysis`, `final_result`) for real-time progress indicators in the UI.
- `GET /api/v1/health`: Health status endpoint for container probes and model health checks.

### 6.2 Frontend Interface (React + Vite UI)
The included web UI (`ui/`) provides a modern dark-mode dashboard:
1. **Header Component**: Displays live Tenant ID, User ID, and Model Health indicator modal.
2. **Query Bar**: Pre-loaded with standard financial queries ("Total revenue this year", "Show P&L statement", "Where is bank reconciliation?").
3. **Response Viewer Tabs**:
   - **Answer Tab**: Formatted Markdown answer with bullet points and financial tables.
   - **Data Payload Tab**: Raw JSON retrieved from Accutax API or SQL query.
   - **Pipeline Routing Tab**: Visual breakdown of Intent Type (1-7), Routing Path, Endpoint called, Complexity rating, and Bedrock model used.
   - **Metrics Tab**: Detailed breakdown of input tokens, output tokens, total LLM calls, total cost in USD, and elapsed time in seconds.

---

## 7. Non-Functional & Operational Requirements

### 7.1 Security & Compliance
- **Authentication**: JWT validation against Accutax Auth Service.
- **OWASP LLM Top 10 Protections**:
  - Prompt Injection Prevention: System prompts separate system instructions from un-trusted user input.
  - Sensitive Information Disclosure: Automated PII redaction layer.
  - Insecure Output Handling: Markdown output sanitization.

### 7.2 Scalability & Cost Management
- **Async Execution**: Built on FastAPI and Uvicorn with async HTTP clients (`httpx`) to handle concurrent requests.
- **Cost Metrics Tracking**: Logs per-request cost in USD based on exact token pricing tiers:
  - *Gemini 2.5 Flash*: $0.075 / 1M input tokens, $0.30 / 1M output tokens.
  - *Claude 3.5 Haiku*: $1.00 / 1M input tokens, $5.00 / 1M output tokens.
  - *Claude 3.5 Sonnet*: $3.00 / 1M input tokens, $15.00 / 1M output tokens.

---

## 8. Development & Implementation Roadmap

- **Phase 1: Core Engine**: 7-Intent Router, Gemini 2.5 Flash Integration, Accutax REST API Catalog & HTTP Client.
- **Phase 2: Reasoning & Fallback**: AWS Bedrock Claude Integration, PostgreSQL NL-to-SQL Fallback Engine.
- **Phase 3: Service & UI**: FastAPI Service, SSE Streaming Endpoint, React/Vite Web UI & Token Cost Dashboard.
- **Phase 4: Future Production Expansion**: Automated VAT 201 Return Assistant, Voice Query Integration & Anomaly Detection.

---

## 9. Appendix: Environment Configuration Reference

```ini
# Gemini API Key
GEMINI_API_KEY=AIzaSy...

# AWS Bedrock Settings
BEDROCK_REGION=ap-south-1
BEDROCK_MODEL_ID=apac.anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_MODEL_ID_FAST=anthropic.claude-3-haiku-20240307-v1:0

# Accutax Backend Base URL & Credentials
ACCUTAX_BASE_URL=http://13.127.157.108:8081/api
ACCUTAX_AUTH_TOKEN=eyJhbGci...
ACCUTAX_USER_ID=18

# PostgreSQL Read-Only Database Connection (NL-to-SQL Fallback)
DB_HOST=13.127.157.108
DB_PORT=5432
DB_NAME=accutax_bk_1_5
DB_USER=postgres
DB_PASSWORD=********
```
