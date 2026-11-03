# Gemini Brain / Accutax AI

Hybrid AI orchestration for Accutax: Gemini routes intents, Accutax REST + PostgreSQL supply data, Claude on Bedrock reasons.

## Layout

```
accutax-ai/
├── frontend/          # React + Vite UI (Auth0, streaming, response blocks)
├── backend/           # FastAPI package (gemini_brain)
│   ├── src/gemini_brain/
│   ├── tests/
│   ├── sql/
│   ├── scripts/{ops,eval,archive}/
│   ├── deploy/
│   ├── pyproject.toml
│   └── server.py
├── docs/
│   ├── product/       # Living product docs
│   └── archive/       # One-off audits / phase notes
├── .env               # Secrets (gitignored) — loaded from repo root
└── .env.example
```

This repo is a **sibling service** to `accutax-backend` / `accutax-frontend`. It talks over HTTP and shared Postgres; it is not embedded in the Nest/React apps.

## Quick start

### Backend

```bash
cd backend
python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -e ".[dev]"
# from repo root: ensure .env exists
cd ..
python backend/server.py --reload
```

API: `http://localhost:8000` · Swagger: `/docs` · Health: `/api/v1/health`

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: `http://localhost:5174` (Vite proxies `/api` → backend; 5174 avoids clashing with `accutax-frontend` on 5173).

## Deploy

See `backend/deploy/provision.sh` and systemd unit `backend/deploy/gemini-brain-api.service`.
Nginx serves `frontend/dist` and proxies `/api` to uvicorn on port 8010.

## MCP connector (ChatGPT / Claude)

`gemini_brain.mcp_server` exposes Accutax AI as read-only MCP tools (`list_organizations`, `ask_accutax`) over
Streamable HTTP. It runs as its own process and calls this API with the caller's own bearer, so tenant
isolation, usage limits and the org-access audit log are the API's.

```bash
cd backend
pip install -e ".[mcp]"
MCP_UPSTREAM_URL=http://127.0.0.1:8000 python -m gemini_brain.mcp_server   # serves http://127.0.0.1:8012/mcp
```

Phase 1 auth is a pasted Accutax AI bearer (`Authorization: Bearer <token>`), enough for MCP Inspector and
Claude Code. ChatGPT and claude.ai need OAuth and a public HTTPS URL (Phase 2): set `MCP_PUBLIC_URL`,
`MCP_ISSUER_URL` and `MCP_ALLOWED_HOSTS` for the domain.

## Tests

```bash
cd backend
pytest
```

## Docs

- Product: `docs/product/`
- Restructure notes: `docs/product/RESTRUCTURE_PLAN.md`
- Archived audits / latency phases: `docs/archive/`
