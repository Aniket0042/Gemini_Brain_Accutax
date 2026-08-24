# Gemini Brain — Complete Technical Documentation & Codebase Reference

---

## 1. Executive Summary & Architecture Overview

**Gemini Brain** is an enterprise-grade AI orchestration subsystem embedded within **Accutax**—a cloud-based bookkeeping and financial management software platform designed for SMEs, accountants, and financial advisors in the GCC region (UAE/Middle East context with 5% VAT and AED currency).

Gemini Brain transforms traditional, menu-driven accounting software into a **Conversational Financial Intelligence Engine**. Users ask natural language questions ("What is our total net profit this year after VAT?", "Where do I record a journal entry?", "Analyze our cash flow risk for Q3") and receive instant, verified answers backed by live accounting data.

### Hybrid Dual-LLM Orchestration Model

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

1. **Google Gemini 2.5 Flash** acts as the high-speed **Orchestrator**: handles 7-type intent classification, endpoint selection, complexity judging, parameter normalization, dynamic org resolution, conversation auto-titling, and direct conversational Q&A for guidance/FAQ.
2. **Anthropic Claude on AWS Bedrock** acts as the deep **Financial Reasoning Engine**: performs complex data analysis, trend extrapolation, ratio calculation, and strategic financial advice over live Accutax API responses. Supports Claude 3.5 Sonnet (`apac.anthropic.claude-3-5-sonnet-20241022-v2:0`), Claude 3.5 Haiku, and Claude 3 Haiku.
3. **Accutax REST API Backend** serves as the **Single Source of Truth**, with an automated **PostgreSQL NL-to-SQL Fallback Engine** when API endpoints are missing or return no data.

---

## 2. Codebase Directory Structure & Layout

```
Gemini_Brain/
├── docs/
│   ├── PRD.md                                 # Product Requirement Document
│   └── GEMINI_BRAIN_COMPLETE_DOCUMENTATION.md # This comprehensive technical reference
├── pyproject.toml                             # Package dependencies & metadata
├── server.py                                  # Uvicorn ASGI server entry point
├── run_demo.py                                # CLI interactive test runner
├── src/
│   └── gemini_brain/
│       ├── __init__.py                        # Top-level exports
│       ├── api/                               # FastAPI web layer
│       │   ├── app.py                         # FastAPI factory, OpenAPI spec & CORS
│       │   ├── auth.py                        # JWT auth, password hashing, tenant security
│       │   ├── models.py                      # Pydantic request/response schemas
│       │   └── routes.py                      # REST & SSE streaming endpoints
│       ├── api_client/                        # Backend REST API client
│       │   └── accutax_client.py              # Requests-based HTTP client for Accutax backend
│       ├── classification/                    # Intent routing
│       │   └── intent_classifier.py           # 7-type intent router via Gemini 2.5 Flash
│       ├── config/                            # Package configuration & catalogs
│       │   ├── api_catalog.py                 # Text-based REST API endpoint catalog
│       │   ├── constants.py                   # Global system constants & prompt IDs
│       │   ├── pricing.py                     # Token pricing tables & cost calculators
│       │   └── settings.py                    # Pydantic BaseSettings environment loader
│       ├── endpoints/                         # API selector & normalizers
│       │   ├── endpoint_selector.py           # Gemini-driven API endpoint selector
│       │   ├── keyword_fallback.py            # Rule-based fallback for missed endpoints
│       │   └── param_normalizer.py            # Parameter type & date normalizer
│       ├── health/                            # Model diagnostics & health checks
│       │   └── model_health_checker.py        # Multi-service live ping & latency probe
│       ├── memory/                            # Conversation history & state
│       │   ├── schema.py                      # Database schema definitions
│       │   ├── session_memory.py              # PostgreSQL session persistence & auto-titling
│       │   └── state_extractor.py             # Hybrid state & entity tracking
│       ├── orchestrator/                      # Pipeline execution core
│       │   └── gemini_brain_runner.py         # Main orchestration pipeline class
│       ├── pii/                               # Security & compliance
│       │   └── redactor.py                    # Presidio PII anonymizer (UAE specific)
│       ├── reasoning/                         # Claude financial analysis
│       │   ├── bedrock_client.py              # AWS Bedrock API client
│       │   ├── claude_reasoner.py             # Financial analyst reasoning prompt & runner
│       │   └── complexity_judge.py            # Flash-driven model tier switcher
│       ├── sql_fallback/                      # PostgreSQL NL-to-SQL fallback
│       │   ├── answer_cleaner.py              # Answer cleanup & thinking strip
│       │   ├── cost_optimizer.py              # Tool call compaction
│       │   ├── db_connection.py               # psycopg2 connection manager
│       │   ├── fast_path.py                   # Pattern-matching query fast path
│       │   ├── sql_engine.py                  # Fallback loop & AST tenant isolation rewriter
│       │   └── sql_safety.py                  # Read-only AST operation validator
│       ├── tenant/                            # Multi-tenant security
│       │   └── org_resolver.py                # Query entity org extraction & DB lookup
│       └── utils/                             # Shared helpers
│           ├── json_parser.py                 # Markdown JSON extractor
│           └── logger.py                      # Logging initializer
├── tests/                                     # Automated test suite
│   ├── test_tenant_isolation_api.py           # Multi-tenant security API integration tests
│   ├── parity/                                # System parity suites
│   │   └── run_parity_suite.py
│   └── unit/                                  # Unit tests
│       ├── test_api_routes.py
│       ├── test_config.py
│       ├── test_json_parser.py
│       ├── test_keyword_fallback.py
│       ├── test_model_health.py
│       ├── test_pii_pipeline.py
│       ├── test_pii_redactor.py
│       └── test_security_and_org.py
└── ui/                                        # Modern React + Vite Web UI
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx                            # Main dashboard application
        ├── index.css                          # Dark-mode design system styles
        ├── components/                        # UI Components
        │   ├── Header.jsx                     # Top bar with model status & auth triggers
        │   ├── LoginPage.jsx                  # Standalone authentication page
        │   ├── ModelHealthModal.jsx           # Live model health diagnostic modal
        │   ├── QueryInput.jsx                 # Financial query input bar with shortcuts
        │   ├── ResponseView.jsx               # Response tab viewer (Answer, Payload, Trace, Metrics)
        │   └── TenantLoginModal.jsx           # Active tenant switching modal
        └── services/
            └── api.js                         # Axios & EventSource HTTP/SSE client
```

---

## 3. Core Modules & Implementation Details

### 3.1 7-Type Intent Classification Engine (`classification/intent_classifier.py`)
Incoming queries are classified into exactly one of 7 intent categories by **Google Gemini 2.5 Flash**:

| Type | Intent Name | Description | Routing Path | Target SLA |
| :---: | :--- | :--- | :---: | :---: |
| **1** | **FAQ / How-to** | Procedural instructions for platform usage | Left Path | $< 1.0\text{s}$ |
| **2** | **App Guidance** | Navigation assistance within Accutax UI | Left Path | $< 1.0\text{s}$ |
| **3** | **Report Generation** | Structured financial statements (P&L, Balance Sheet) | Right Path | $< 2.5\text{s}$ |
| **4** | **Data Query** | Live lookup of financial data (revenue, invoices, expenses) | Right Path | $< 2.0\text{s}$ |
| **5** | **Forecast & Prediction**| Projections & future trend estimations | Right Path | $< 2.5\text{s}$ |
| **6** | **Accounting Concept**| Definitions and standards (VAT, AR, AP, Ledger) | Left Path | $< 1.0\text{s}$ |
| **7** | **Strategic Summary** | Executive business advice & health checks | Left Path / Right | $< 2.0\text{s}$ |

- **Left Path (Types 1, 2, 6, 7)**: Directly answered by Gemini 2.5 Flash using `DIRECT_ANSWER_SYSTEM_PROMPT` tailored for Middle East UAE accounting context (5% VAT, AED currency). No backend API call required.
- **Right Path (Types 3, 4, 5)**: Triggers endpoint selection and backend REST API execution.

### 3.2 Endpoint Selection & Parameter Normalization (`endpoints/`, `config/api_catalog.py`)

#### Dynamic REST Endpoint Catalog
When a Right Path query is received, **Gemini 2.5 Flash** inspects `API_CATALOG` to select the optimal Accutax REST API endpoint:
- **Invoices / Income**: `GET /income/list`, `GET /income/find`, `GET /income/total`, `GET /income/customer-payment/list`
- **Expenses / Bills**: `GET /expense/list`, `GET /expense/find`, `GET /expense/total`, `GET /expense/supplier-payment/list`
- **Banking**: `GET /bank/manual/accounts`, `GET /bank/transactions/uncategorized`, `GET /bank/rules`
- **Contacts**: `GET /contact/list` (`contact_type_id`: 4=customer, 1,2,3=vendor), `GET /contact/find`
- **Chart of Accounts**: `GET /chart-of-accounts`, `GET /chart-of-accounts/list`
- **Dashboard**: `GET /dashboard/web/v3`
- **Accounting**: `GET /accounting/journal-entries`, `GET /accounting/general-ledger`
- **Audit**: `GET /audit-logs`, `GET /audit-trails`
- **Financial Reports**: `GET /report/profit-loss`, `GET /report/balance-sheet`, `GET /report/cash-flow`, `GET /report/cash-forecast`, `GET /report/ar-aging-summary`, `GET /report/ap-aging-summary`, `GET /report/customer-balance-summary`, `GET /report/expense-by-category`, `GET /report/sales-by-customer`
- **Items & Currency**: `GET /item/list`, `GET /currency/supported`, `GET /currency/exchange-rates`, `GET /currency/convert`
- **Organizational**: `GET /branches`, `GET /cost-centers`, `GET /projects/list`

#### Keyword Fallback Engine (`keyword_fallback.py`)
If Gemini's zero-shot endpoint selection fails or returns null for common financial phrases, a deterministic keyword matcher resolves the endpoint:
- "total sales", "total revenue", "income total" $\rightarrow$ `/income/total`
- "total expenses", "total spending", "bills total" $\rightarrow$ `/expense/total`
- "profit and loss", "p&l", "net profit" $\rightarrow$ `/report/profit-loss`
- "balance sheet", "assets liabilities" $\rightarrow$ `/report/balance-sheet`
- "cash flow statement" $\rightarrow$ `/report/cash-flow`
- "overdue invoices", "aging report", "who owes us" $\rightarrow$ `/report/ar-aging-summary`
- "uncategorized bank" $\rightarrow$ `/bank/transactions/uncategorized`

#### Parameter Normalizer (`param_normalizer.py`)
Normalizes API parameters to prevent 400 Bad Request errors from the Accutax backend:
- Ensures `userId` (camelCase) is supplied for `/income/list`, `/expense/list`, and `/accounting/journal-entries`.
- Enforces `user_id` as a string (e.g. `"18"`) for `/item/list`.
- Formats `/income/total` and `/expense/total` params: `user_id="18"`, `filter_year="2026"`, `filter_type="YEARLY"`.
- Converts date shortcuts ("this month", "this year", "this quarter") to `YYYY-MM-DD` strings.

### 3.3 Accutax REST API Client (`api_client/accutax_client.py`)
Performs live GET calls against the Accutax backend (`ACCUTAX_BASE_URL`):
- Handles HTTP Bearer token authentication headers.
- Enforces timeout budgets (`HTTP_TIMEOUT = 8.0s`).
- Unrolls `sendSuccessResponse` envelope shapes (`{"success": true, "data": [...]}`) via `extract_data()`.

### 3.4 Complexity Judging & Bedrock Reasoning Engine (`reasoning/`)

#### Complexity Judge (`complexity_judge.py`)
Evaluates retrieved API data size and question nuance:
- **SIMPLE / MEDIUM**: Single-metric lookups, short lists $\rightarrow$ routed to **Claude 3.5 Haiku** (`anthropic.claude-3-haiku-20240307-v1:0` or `claude-3-5-haiku-20241022-v1:0`).
- **COMPLEX**: Forecasting, multi-period trend comparison, ratio calculation, cross-dataset synthesis $\rightarrow$ routed to **Claude 3.5 Sonnet** (`apac.anthropic.claude-3-5-sonnet-20241022-v2:0`).

#### Bedrock Adapter & Reasoning (`bedrock_client.py`, `claude_reasoner.py`)
Wraps AWS Bedrock `invoke_model` API:
- Passes live API response payload along with `ANALYST_SYSTEM_PROMPT`.
- Embeds project context and cross-chat history if available.
- Evaluates input/output token usage and calculates USD execution cost using exact model pricing tiers.

### 3.5 PostgreSQL NL-to-SQL Fallback Engine (`sql_fallback/`)
If an API endpoint does not exist for a query or returns a 404/500 HTTP error:
1. **Fast Path Check (`fast_path.py`)**: Checks for simple pattern matches (e.g. count queries).
2. **SQL Safety AST Validation (`sql_safety.py`)**: Rejects non-`SELECT` statements (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`).
3. **AST & Regex Tenant Isolation Rewriter (`enforce_tenant_isolation_sql` in `sql_engine.py`)**:
   - Parses the generated SQL query.
   - Forcibly replaces or injects `organization_id = <org_id>` filters across all tenant tables (`contacts`, `income`, `expense`, `items`, `bank_accounts`, `chart_of_accounts`, `customer_payment`, `supplier_payments`, `projects`, `organizations`).
   - Prevents cross-tenant data leaks even if LLM hallucinated another org ID in the SQL text.
4. **Execution & Synthesis**: Executes read-only SQL via `db_connection.py`, cleans thinking artifacts (`answer_cleaner.py`), compacts tool results (`cost_optimizer.py`), and generates final response.

### 3.6 Multi-Tenant Isolation & JWT Authentication (`api/auth.py`, `tenant/`)

#### Dynamic Organization Resolver (`org_resolver.py`)
Detects if user prompt explicitly names a tenant or database ID (e.g. *"Show invoices for Zero-Config"* or *"organization 27"*), queries `organizations` table via numeric or `ILIKE` fuzzy match, and resolves the target `organization_id`.

#### JWT Auth & Tenant Security Boundary (`api/auth.py`)
- **OAuth2 Bearer Integration**: Compatible with Swagger UI `/docs` Authorize dialog.
- **JWT Token Claims**: Payload contains `sub` (user_id), `email`, `allowed_org_ids` (array of organization IDs user is authorized to access), `iat`, `exp`.
- **FastAPI `get_current_user` Dependency**: Validates incoming bearer tokens.
- **Tenant Scope Enforcement**: `run_query` and `stream_query` endpoints verify requested `organization_id` against `current_user.allowed_org_ids`. Access is rejected with a `400 Bad Request` if a user attempts to access an unauthorized tenant organization.
- **Offline Seed Accounts**: `_SEED_USER_MAP` provides pre-computed accounts (`admin@accutax.com`, `user_single@example.com`, `user_multi@example.com`) to guarantee developer logins even when PostgreSQL tunnel is offline.

### 3.7 Presidio PII Redaction Engine (`pii/redactor.py`)
Integrates Microsoft Presidio (`AnalyzerEngine`, `AnonymizerEngine`) to scrub sensitive PII prior to sending prompts to third-party AI models (Gemini / Bedrock):

| Entity Type | Pattern / Recognizer | Anonymized Replacement |
| :--- | :--- | :--- |
| **Email Address** | Presidio built-in `EMAIL_ADDRESS` | `[EMAIL_REDACTED]` |
| **Phone Number** | Presidio built-in `PHONE_NUMBER` + custom `UAE_PHONE_NUMBER` (`+971 5X...` / `05X...`) | `[PHONE_REDACTED]` |
| **Credit/Debit Card** | Presidio built-in `CREDIT_CARD` (Luhn-validated) | `[CARD_REDACTED]` |
| **IBAN Code** | Presidio built-in `IBAN_CODE` + custom `UAE_IBAN_CODE` (`AE` + 21 digits) | `[IBAN_REDACTED]` |
| **UAE Emirates ID** | Custom `UAE_EMIRATES_ID` (`784-YYYY-XXXXXXX-Z`) | `[ID_REDACTED]` |

Returns redacted text string along with exact entity-type redaction counts.

### 3.8 Session Memory, State Extraction & Titling (`memory/`)
- **Database Persistence**: Stores chat threads in `public.model_arena_chat_sessions` and messages in `public.model_arena_chat_messages`.
- **Session Ownership**: `verify_session_ownership()` enforces user ownership of chat session UUIDs.
- **Auto-Titling**: Automatically generates concise conversation titles (e.g. *"Q1 Revenue & Tax Analysis"*) via Gemini Flash after the second turn (`maybe_auto_title()`).
- **Hybrid State Tracking**: Extracts active state entities (current date range, contact filter, invoice ID) via `update_conversation_state_hybrid_by_session()` to support multi-turn contextual follow-ups ("Now show only unpaid ones").

### 3.9 Live AI Model Diagnostics & Health Checker (`health/model_health_checker.py`)
Performs real-time health pings across all underlying models and services:
- **Google Gemini 2.5 Flash**: API connection and text generation check.
- **AWS Bedrock Claude 3.5 Sonnet**: Bedrock runtime ping.
- **AWS Bedrock Claude 3 Haiku**: Fast model runtime ping.
- **Accutax REST API Backend**: HTTP endpoint reachability check.
- **PostgreSQL Database**: Connection pool check.

Measures latency (ms) for each service and returns a consolidated `ModelHealthResponse`.

### 3.10 REST API & SSE Streaming Server (`api/app.py`, `api/routes.py`, `server.py`)
Built on FastAPI and Uvicorn ASGI server with full CORS middleware support.

#### Endpoints
- `POST /api/v1/auth/login`: Form-encoded login (Swagger UI compatible).
- `POST /api/v1/auth/login-json`: JSON body login (`username`, `password`).
- `GET /api/v1/health`: Basic API service health probe.
- `GET /api/v1/health/models`: Comprehensive health & latency diagnostic check.
- `POST /api/v1/health/models`: Diagnostic check with custom prompt payload.
- `POST /api/v1/query`: Synchronous financial query execution.
- `POST /api/v1/query/stream`: Server-Sent Events (SSE) streaming endpoint (`text/event-stream`) emitting real-time status chunks (`classification`, `endpoint_selection`, `api_call`, `reasoning`, `sql_fallback`, `final_result`).

### 3.11 React + Vite Modern Frontend Dashboard (`ui/`)
Located in `ui/`, built with React 18, Vite, Tailwind CSS / Vanilla CSS dark mode:
- **LoginPage Component**: Seamless email/password authentication screen.
- **Header Component**: Displays active tenant, user profile, logout, clear chat, and Model Diagnostics button.
- **QueryInput Component**: Financial search bar pre-loaded with example shortcuts ("Total sales this year", "Show P&L statement", "Where is bank reconciliation?").
- **ResponseView Tabs**:
  1. **Answer Tab**: Rendered Markdown answer with bullet points and financial tables.
  2. **Data Payload Tab**: Raw JSON payload retrieved from Accutax API or SQL engine.
  3. **Pipeline Routing Tab**: Visual step-by-step trace (Intent Type 1-7, Path, Endpoint, Complexity, Reasoning Model).
  4. **Metrics Tab**: Input tokens, output tokens, total LLM calls, USD cost, and elapsed seconds.
- **ModelHealthModal Component**: Displays live PING status, green/red health indicators, and latency for Gemini Flash, AWS Bedrock Sonnet/Haiku, Accutax API, and PostgreSQL DB.

---

## 4. PRD vs Implementation Parity Analysis

| Feature / Requirement Area | PRD Status (`docs/PRD.md`) | Codebase Implementation | Parity & Drift Analysis |
| :--- | :--- | :--- | :--- |
| **Dual-LLM Architecture** | Gemini Flash (Orchestrator) + Claude (Bedrock Reasoner) | Implemented in `orchestrator/gemini_brain_runner.py` & `reasoning/bedrock_client.py` | **100% Aligned**. Gemini 2.5 Flash + Bedrock Claude Sonnet/Haiku. |
| **7-Type Intent Classification** | 7 Intent types defined (Types 1-7) | Implemented in `classification/intent_classifier.py` | **100% Aligned**. Prompt & router logic match PRD. |
| **Accutax REST API Catalog** | 14 basic endpoints listed | Expanded in `config/api_catalog.py` (22+ endpoints including financial reports, items, currencies) | **Enhanced Beyond PRD**. Codebase catalog covers full financial reporting suite. |
| **Keyword Fallback Engine** | Not mentioned in PRD | Implemented in `endpoints/keyword_fallback.py` | **Code-First Extension**. Resolves queries where Gemini selector returns null. |
| **Parameter Normalizer** | Basic parameter mapping mentioned | Implemented in `endpoints/param_normalizer.py` | **Enhanced Beyond PRD**. Adds camelCase `userId` & string `filter_year` formatting. |
| **Complexity Judging** | SIMPLE/MED (Haiku) vs CMPLX (Sonnet) | Implemented in `reasoning/complexity_judge.py` | **100% Aligned**. Dynamic tier switching based on data payload & prompt. |
| **PostgreSQL NL-to-SQL Fallback** | Read-only SQL fallback on missing API | Implemented in `sql_fallback/sql_engine.py` & `sql_safety.py` | **100% Aligned**. Read-only validation active. |
| **SQL Tenant Isolation Rewriter** | PRD specifies `WHERE organization_id = :org_id` | Implemented `enforce_tenant_isolation_sql()` in `sql_engine.py` | **Enhanced Security**. AST & regex rewriter forcibly injects/replaces `organization_id` on all tenant tables. |
| **JWT Auth & Tenant Scoping** | General JWT mentioned | Implemented in `api/auth.py` with OAuth2 Swagger login & `allowed_org_ids` claim | **Enhanced Security**. REST endpoints validate JWT & block cross-tenant queries. |
| **Presidio PII Redaction** | General PII redaction mentioned | Implemented in `pii/redactor.py` with UAE Emirates ID, UAE Phone, UAE IBAN | **Enhanced Security**. Specialized GCC/UAE pattern recognizers active. |
| **Session Memory & Auto-Titling** | UUID session memory & auto-titling | Implemented in `memory/session_memory.py` & `state_extractor.py` | **100% Aligned**. PostgreSQL message persistence & title generation. |
| **Live Model Health Diagnostics** | PRD mentions `/api/v1/health` | Implemented `/api/v1/health/models` endpoint & UI modal | **Code-First Extension**. Live PING & latency diagnostic check across 5 services. |
| **REST API & SSE Streaming** | `/query`, `/query/stream`, `/health` | Implemented in `api/routes.py` & `api/app.py` | **100% Aligned**. FastAPI + SSE `text/event-stream` active. |
| **React + Vite Web UI** | Dark-mode UI with 4 response tabs | Implemented in `ui/` (React + Vite + Tailwind/CSS) | **100% Aligned**. Answer, Payload, Trace, and Metrics tabs fully active. |

### PRD Update Assessment
Is `docs/PRD.md` up to date?
- **Core Vision & Architecture**: The PRD accurately describes the overall vision, 7-intent routing model, dual-LLM orchestration, and high-level workflows.
- **Areas Needing Documentation Updates in PRD**:
  1. **API Catalog**: PRD Section 5.2 should be updated to reflect the expanded endpoints (`/report/profit-loss`, `/report/balance-sheet`, `/report/cash-flow`, `/report/ar-aging-summary`, `/report/ap-aging-summary`, `/report/customer-balance-summary`, `/report/expense-by-category`, `/report/sales-by-customer`, `/item/list`, `/currency/supported`).
  2. **Security & JWT Auth Specs**: PRD Section 7.1 should specify the exact `/api/v1/auth/login` OAuth2 flow, `allowed_org_ids` claim, and `enforce_tenant_isolation_sql()` AST rewriter.
  3. **UAE PII Recognizers**: PRD Section 5.5 should list the custom Presidio recognizers for UAE Emirates ID (`784-YYYY-XXXXXXX-Z`), UAE Phone (+971 5X), and UAE IBAN.
  4. **Health Diagnostics Endpoint**: PRD Section 6.1 should include the `/api/v1/health/models` multi-target PING diagnostic endpoint.

---

## 5. Summary of Key Files

| Module File | Purpose & Responsibilities |
| :--- | :--- |
| [`server.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/server.py) | Uvicorn ASGI server launcher for FastAPI app with CLI host/port flags. |
| [`src/gemini_brain/orchestrator/gemini_brain_runner.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/orchestrator/gemini_brain_runner.py) | Main orchestration engine coordinating intent classification, endpoint selection, API calls, complexity judging, Bedrock reasoning, SQL fallback, and SSE streaming. |
| [`src/gemini_brain/api/app.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/api/app.py) | FastAPI app factory configuring Swagger UI (`/docs`), ReDoc (`/redoc`), CORS origins, and startup lifespan. |
| [`src/gemini_brain/api/routes.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/api/routes.py) | FastAPI router providing `/auth/login`, `/health`, `/health/models`, `/query`, and `/query/stream`. |
| [`src/gemini_brain/api/auth.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/api/auth.py) | JWT token creation/decoding, bcrypt password hashing, seed user fallback map (`_SEED_USER_MAP`), and tenant isolation dependency (`get_current_user`). |
| [`src/gemini_brain/classification/intent_classifier.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/classification/intent_classifier.py) | 7-Type intent classification engine powered by Google Gemini 2.5 Flash. |
| [`src/gemini_brain/endpoints/endpoint_selector.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/endpoints/endpoint_selector.py) | Gemini-driven REST API endpoint selector with date window resolution. |
| [`src/gemini_brain/endpoints/keyword_fallback.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/endpoints/keyword_fallback.py) | Rule-based fallback for financial report endpoints. |
| [`src/gemini_brain/endpoints/param_normalizer.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/endpoints/param_normalizer.py) | Normalizes endpoint query parameters (`userId`, string `filter_year`, `filter_type`). |
| [`src/gemini_brain/api_client/accutax_client.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/api_client/accutax_client.py) | Requests-based HTTP client for calling Accutax backend REST API endpoints. |
| [`src/gemini_brain/reasoning/complexity_judge.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/reasoning/complexity_judge.py) | Dynamic complexity classifier (SIMPLE, MEDIUM, COMPLEX) for model switching. |
| [`src/gemini_brain/reasoning/bedrock_client.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/reasoning/bedrock_client.py) | AWS Bedrock API wrapper for Claude 3.5 Sonnet / Haiku with token cost metrics. |
| [`src/gemini_brain/reasoning/claude_reasoner.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/reasoning/claude_reasoner.py) | System prompt & execution runner for Claude financial data analysis. |
| [`src/gemini_brain/sql_fallback/sql_engine.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/sql_fallback/sql_engine.py) | PostgreSQL NL-to-SQL fallback runner with AST tenant isolation rewriter (`enforce_tenant_isolation_sql`). |
| [`src/gemini_brain/sql_fallback/sql_safety.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/sql_fallback/sql_safety.py) | Read-only SQL safety validator rejecting write operations. |
| [`src/gemini_brain/tenant/org_resolver.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/tenant/org_resolver.py) | Dynamic organization extractor and database lookup module. |
| [`src/gemini_brain/pii/redactor.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/pii/redactor.py) | Presidio PII anonymizer with custom recognizers for UAE Emirates ID, UAE Phone, UAE IBAN, Credit Card, Email. |
| [`src/gemini_brain/memory/session_memory.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/memory/session_memory.py) | PostgreSQL chat history persistence, session ownership verification, and auto-titling. |
| [`src/gemini_brain/health/model_health_checker.py`](file:///c:/Users/acer/Desktop/Gemini_Brain/src/gemini_brain/health/model_health_checker.py) | Real-time diagnostic status & latency checker for Gemini, Bedrock, Accutax API, and DB. |
| [`ui/src/App.jsx`](file:///c:/Users/acer/Desktop/Gemini_Brain/ui/src/App.jsx) | React 18 frontend application serving the dark-mode dashboard, SSE streaming, authentication, active tenant switcher, and response viewers. |
