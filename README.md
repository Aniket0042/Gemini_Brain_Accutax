# Accutax AI

The AI assistant for Accutax (UAE bookkeeping and VAT). A tool-using agent on **AWS Bedrock (Claude)** answers questions about an organization's books from **Cube** figures, about UAE VAT law from a **knowledge base of Federal Tax Authority documents**, and about the app from the **user guide**. It also builds report files, reads attached documents and runs scheduled "bots".

> Historical note: earlier versions routed questions through a Google Gemini 7-intent router with REST endpoint selection and NL-to-SQL. That design was removed. Gemini is not used anywhere in the code. The Python package is still called `gemini_brain` for that reason. `docs/product/GEMINI_BRAIN_COMPLETE_DOCUMENTATION.md` and `END_TO_END_FLOW_BY_INTENT.md` describe the removed design.

## How it answers

```
Browser (assistant frontend)
   │  session cookie
   ▼
Accutax API (Nest, accutax-backend)  ── /ai/api/v1/* gateway: checks the session and the user's companies
   │  Authorization: Bearer <gatewayToAiKey> + one-time delegation header
   ▼
This service (FastAPI)  ── POST <api>/internal/ai/resolve  →  { user_id, allowed_org_ids }
   │
   └─► agent loop (backend/src/gemini_brain/agent/loop.py)
         ├─ VAT law question, no files  → law route: knowledge-base passages + cited answer (agent/law.py, vat_kb/)
         └─ everything else → bounded tool loop on Bedrock (planner model chooses tools, answer model writes):
               query_metrics, list_documents, cash_forecast   figures from Cube (semantic/)
               review_vat, vat_return, vat_penalty            VAT review and return figures, penalty calculator
               search_vat_kb                                  UAE VAT law with numbered FTA sources
               app_guide                                      how to do something in the app (knowledge/accutax_guide.md)
               read_document                                  more of a file attached in this chat
```

Key properties, all enforced in code:

- **Figures come only from tools.** The model never gets database access and cannot write SQL. `query_metrics` names a Cube view, measures, groupings, filters and a period; every name is checked against the Cube catalog.
- **Tenant scope comes from the request, never from the model.** The tool context holds the organizations the user selected (and is allowed by the gateway); the model can only narrow that set.
- **Hard limits.** `AGENT_MAX_TOOL_CALLS` tool calls (default 6) and `AGENT_DEADLINE_SECONDS` for the whole question (default 45). Tool calls from one turn run in parallel.
- **Guards.** A verifier checks the figures in the answer against tool results (report only); order-like text found in books or files is flagged to the model and logged; a canary marker detects prompt leaks; citations to FTA documents are checked against the sources actually found.
- **Privacy.** `PII_MODE=on` sends the model placeholders instead of names and identifiers and puts the real values back in the answer (`backend/src/gemini_brain/privacy/`).
- **Usage metering.** Spend is recorded per owner, with optional limits (`USAGE_*`).

### Authentication

`AI_AUTH_MODE=gateway` is the production mode (`NODE_ENV=production` refuses to start otherwise). Browser cookies and product JWTs terminate at the Nest API. This service accepts only requests carrying the private `gatewayToAiKey` plus a one-time delegation, rejects any request with a cookie or `Origin` header, and asks the API (`resolveUrl`) who the user is and which companies they may use on every call. CORS is closed in this mode. The keys live in a private JSON file (`AI_GATEWAY_CONFIG_FILE`, mode 0600); see `docs/AI_SERVER_SETUP.md` in the workspace.

`AI_AUTH_MODE=legacy` (the default when unset) keeps the old own-login, JWT and handoff routes. It exists for isolated local work only.

## Layout

```
accutax-ai/
├── frontend/            React + Vite assistant UI (static site). Talks to the Nest API at VITE_BFF_API_URL under /ai/...
├── backend/             Python package gemini_brain (FastAPI), tests, scripts, legacy deploy notes
│   ├── src/gemini_brain/
│   │   ├── agent/         tool loop, tools, prompt, profiles, history, audit, answer log
│   │   ├── api/           routes, gateway authority, legacy auth, bots, usage, privacy routes
│   │   ├── semantic/      Cube client, catalog, query_metrics, list_documents, cash_forecast
│   │   ├── vat_kb/        FTA knowledge base: crawl, index, retrieve, cite
│   │   ├── vat_review/    VAT review checks   vat_return/  VAT return figures and penalties
│   │   ├── bots/          scheduled agents, inbox, approvals   artifacts/  report files
│   │   ├── uploads/       documents attached in chat   privacy/ pii/  masking   usage/  metering and limits
│   │   ├── reasoning/     Bedrock client   config/  settings (every environment variable)
│   │   └── knowledge/     the app user guide
│   ├── tests/           unit/, integration/, report_golden/
│   ├── scripts/         ops/, eval/, archive/ (some archive scripts import removed modules), accutax_seed/
│   ├── sql/ dba/        SQL functions and table scripts
│   ├── deploy/          LEGACY (old VM). See backend/deploy/README.md
│   └── server.py        uvicorn launcher
├── semantic/            Cube Core: model/cubes, model/views, cube.js, docker compose, deploy, read-only DB role SQL
├── docs/                design notes (product/ and archive/ are historical, see their banners)
├── .env                 your configuration (git-ignored), loaded from this directory
└── .env.example         every variable by name; copy to .env
```

## Run it locally

You need Python 3.10+, Node, a PostgreSQL database, a running Cube instance (`semantic/`) for figures, and AWS credentials that can call Bedrock in the configured region.

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "backend[dev]"          # add ,vat_kb for the VAT knowledge base: pip install -e "backend[dev,vat_kb]"
cp .env.example .env                   # then fill it in; the settings file reads accutax-ai/.env, not backend/.env
python backend/server.py --reload      # http://localhost:8000
```

Swagger is at `/docs`, the public health route at `/api/v1/health`. Without `CUBE_API_SECRET` or a reachable Cube, only the law and app-guide tools are offered.

To run in gateway mode locally (the same path as production), start the identity stack and the Nest API from `accutax-backend` (`npm run identity:up`, `npm run start:dev`), then set `AI_AUTH_MODE=gateway` and point `AI_GATEWAY_CONFIG_FILE` at the local gateway key file the Nest side uses. The workspace `.claude/launch.json` entry `accutax-ai-backend` does this on port 8000.

### Cube (figures)

```bash
cd semantic
cp cube.env.example cube.env           # fill in the read-only database role and CUBEJS_API_SECRET
docker compose -f docker-compose.cube.yml up -d
```

`CUBE_API_SECRET` in the backend `.env` must equal `CUBEJS_API_SECRET` in `semantic/cube.env`. Cube listens on loopback only.

### Frontend

```bash
cd frontend
npm install
npm run dev -- --port 5175 --strictPort    # or just: npm run dev (the port is pinned to 5175)
```

UI: `http://localhost:5175`. The frontend does not use a Vite proxy: it signs in through the Hub (`VITE_HUB_URL`, default `http://localhost:5176`) and sends every request to the Nest API (`VITE_BFF_API_URL`, default `http://localhost:8081`), which forwards `/ai/api/v1/...` to this service. Port 5175 is the one the Nest CORS and gateway configuration, the Hub and the frontend's `VITE_AI_CHATBOT_URL` expect (5173 is the bookkeeping app). Variables are listed in `frontend/.env.example`.

## Configuration

All settings are declared in `backend/src/gemini_brain/config/settings.py` and listed, by name, in `.env.example`. Groups:

| Group | Variables |
|---|---|
| Runtime and auth | `NODE_ENV`, `PORT`, `API_HOST`, `API_PORT`, `AI_AUTH_MODE`, `AI_GATEWAY_CONFIG_FILE`, `CORS_ORIGINS`, legacy: `ACCUTAX_BASE_URL`, `ACCUTAX_APP_URL`, `JWT_SECRET`, `ACCUTAX_JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRATION_MINUTES`, `HANDOFF_SERVICE_KEY`, `SEED_TEST_USERS` |
| Bedrock | `BEDROCK_REGION`, `BEDROCK_MODEL_ID`, `BEDROCK_MODEL_ID_FAST`, `BEDROCK_MAX_TOKENS`, `BEDROCK_*_TIMEOUT_SECONDS`, `BEDROCK_MAX_ATTEMPTS` (AWS credentials come from the default AWS chain) |
| Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_NAME_ALLOWLIST`, `DB_POOL_ENABLED`, `DB_POOL_MAX` |
| Cube | `CUBE_API_URL`, `CUBE_API_SECRET`, `CUBE_JWT_AUDIENCE`, `CUBE_JWT_ISSUER`, `REPORT_TIMEZONE` |
| Agent | `AGENT_MODEL_ID`, `AGENT_ANSWER_MODEL_ID`, `AGENT_MODELS`, `AGENT_MODEL_USERS`, `AGENT_MAX_TOOL_CALLS`, `AGENT_DEADLINE_SECONDS`, `AED_RATES_FILE`, `AGENT_AUDIT_LOG`, `AGENT_ANSWER_LOG`, `MAX_ORGS_PER_QUERY`, `MULTI_ORG_ENABLED`, `SHOW_*_TRACES`, `MODEL_DIAGNOSTIC_ADMINS` |
| VAT knowledge base | `VAT_KB_ENABLED`, `VAT_KB_SHADOW`, `VAT_KB_DIR`, `VAT_KB_MODEL_ID`, `VAT_KB_TIMEOUT_SECONDS` |
| Reports and uploads | `REPORT_*`, `ARTIFACT_*`, `UPLOADS_ENABLED`, `UPLOAD_*` |
| Bots | `BOT_SERVICE_TOKEN`, `BOT_SCHEDULER_ENABLED`, `BOT_SCHEDULER_INTERVAL_SECONDS` |
| Usage | `USAGE_LEDGER`, `USAGE_DB`, `USAGE_ENFORCE`, `USAGE_TIMEZONE`, `USAGE_LITE_MODEL`, `USAGE_PLATFORM_*`, `USAGE_RATE_PER_MINUTE`, `USAGE_ADMINS` |
| Privacy | `PII_MODE`, `PII_SENTINEL`, `PII_SNAPSHOTS`, `PII_BADGE`, `PII_CARD_GUARD`, `PII_SNAPSHOT_*`, `PII_DEMO_ORGS`, `PII_SENTINEL_LOG` |

## API surface

Everything is under `/api/v1`: `POST /query` and `/query/stream` (the agent), `/sessions` (chat threads and messages), `/tenants` (the user's companies), `/models` and `/agents` (what the UI offers), `/artifacts/{id}` (generated files), `/uploads` (attached files), `/health`, plus `/bots/*`, `/usage/*` and `/privacy/*`. In gateway mode the user-facing routes need the gateway key and a delegation (the `/bots/tick` and `/bots/event` triggers use `BOT_SERVICE_TOKEN` instead); `/health` is public. `/auth/*` is for legacy mode and is refused in gateway mode.

## Tests

```bash
cd backend
../.venv/bin/python -m pytest tests/unit
```

`tests/integration/test_cube_isolation.py` needs a running Cube. Some tests in the suite already fail independently of any change; compare against a baseline before blaming a change. Frontend: `node --test src/auth/*.test.mjs` from `frontend/`.

## Deploy

Production runs on ECS (see `accutax-infra` and `docs/OPERATIONS.md` in the workspace); the dev AI server is described in `docs/AI_SERVER_SETUP.md`. The files in `backend/deploy/` describe an old shared VM and are legacy. The frontend is a static build (`npm run build`; a production build requires `VITE_BFF_API_URL` to be an HTTPS origin).

## Docs

- `docs/ai-architecture-review-2026-10.md`: why the agent replaced the router (section 6.3 is the agent design)
- `docs/CUBE_CORE_INTEGRATION_GUIDE_V2.md`: the semantic layer
- `docs/product/VAT_KNOWLEDGE_BASE_PLAN.md`: the VAT knowledge base
- `docs/ORG_TOKEN_LIMITS_STRATEGY.md`, `docs/USAGE_LIMITS_DESIGN.md`: usage limits
- `docs/ACCUTAX_AGENTS_DESIGN.md`, `docs/ACCUTAX_BOTS_PLAN.md`: specialist agents and bots
- `docs/product/GEMINI_BRAIN_COMPLETE_DOCUMENTATION.md`, `docs/product/END_TO_END_FLOW_BY_INTENT.md`, `docs/archive/`: historical, describe the removed router design
