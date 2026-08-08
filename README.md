# Gemini Brain

A clean, production-grade Python package and **FastAPI REST API Service** for the **Gemini Brain** hybrid AI orchestration engine.

Gemini Brain routes financial queries between **Google Gemini 2.5 Flash** (routing, intent classification, endpoint selection, complexity judging, direct Q&A) and **Anthropic Claude on AWS Bedrock** (data-driven reasoning over live financial data), using the Accutax REST API as the primary source of truth and PostgreSQL NL-to-SQL as a fallback engine.

---

## 🏗️ Architecture

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

## 📦 Package Structure

```
gemini_brain/
├── pyproject.toml              # Package configuration & dependencies
├── README.md                   # System documentation
├── .env                        # Local environment credentials
├── .env.example                # Environment variables template
├── server.py                   # FastAPI REST API & Swagger UI launcher
├── run_demo.py                 # Quick execution demo script
├── src/
│   └── gemini_brain/
│       ├── __init__.py         # Exposes GeminiBrainRunner & settings
│       ├── api/                # FastAPI web app, routes, Swagger models
│       ├── config/             # Settings, constants, pricing, API catalog
│       ├── utils/              # JSON extraction & logging
│       ├── tenant/             # Dynamic organization/tenant resolver
│       ├── classification/     # 7-type intent classifier
│       ├── endpoints/          # Endpoint selector, param normalizer, keyword fallback
│       ├── api_client/         # Accutax REST API HTTP client
│       ├── reasoning/          # Bedrock client, complexity judge, Claude reasoner
│       ├── sql_fallback/       # DB connection, safety, answer cleaner, fast path, SQL engine
│       ├── memory/             # Session history, DDL schema, hybrid state extractor
│       └── orchestrator/       # GeminiBrainRunner main entry point
└── tests/
    ├── unit/                   # Unit test suite (15 tests covering API, config, secrets, org)
    └── parity/                 # Side-by-side parity comparison test suite
```

---

## 🌐 FastAPI REST API & Swagger UI Service

### Starting the Server

To launch the REST API server with Uvicorn:

```bash
python server.py
# or
python server.py --host 0.0.0.0 --port 8000 --reload
```

### Interactive Documentation URLs

- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **OpenAPI JSON**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

---

## 🚀 API Endpoints

### 1. `POST /api/v1/query` — Synchronous Financial Query
- **Request Body**:
  ```json
  {
    "query": "What is our total revenue this year?",
    "organization_id": 27,
    "user_id": 18,
    "use_api": true
  }
  ```
- **Response**:
  ```json
  {
    "answer": "Total revenue for 2026 is AED 1,250,000.00...",
    "routing_info": {
      "type": 4,
      "type_label": "Data Query",
      "path": "api_then_anthropic",
      "api_endpoint": "/income/total",
      "complexity": "MEDIUM",
      "bedrock_model": "Claude Haiku 4.5"
    },
    "token_usage": {
      "input_tokens": 420,
      "output_tokens": 180,
      "llm_calls": 3,
      "cost_usd": 0.00085,
      "elapsed_seconds": 1.25
    }
  }
  ```

### 2. `POST /api/v1/query/stream` — Streaming SSE Progress
- **Request Body**: Same as `/query`
- **Response Header**: `Content-Type: text/event-stream`
- **Data Stream**: Real-time status steps (`classification`, `retrieval`, `analysis`, `final_result`).

### 3. `GET /api/v1/health` — Health Check
- **Response**: `{"status": "ok", "version": "0.1.0", "service": "gemini-brain-api"}`

---

## 💻 Python Library Usage

```python
from gemini_brain import GeminiBrainRunner

runner = GeminiBrainRunner()

result = runner.run(
    query="What is our total revenue for 2026?",
    organization_id=27,
    user_id=18
)

print("Answer:", result["answer"])
print("Routing Path:", result["routing_info"]["path"])
```

---

## 🧪 Testing

Run unit tests:
```bash
python -m pytest tests/unit
```

Run side-by-side parity tests against the original monolith:
```bash
python tests/parity/run_parity_suite.py
```
