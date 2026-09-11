# Accutax-AI Restructure Plan (approved: 1A · 2C · 3B+C)

**Branch:** `chore/restructure-backend-frontend`  
**Decisions:** Port health UI from `ui/` then delete it · Full deep clean · Refine + execute on branch

## Goals

1. Clear `backend/` + `frontend/` separation inside `accutax-ai`
2. Remove duplicated / deprecated / unused code
3. Keep Accutax Nest/React apps as sibling consumers (HTTP/DB only)
4. Preserve `/api/v1` contract and `gemini_brain` package name

## Target tree

```
accutax-ai/
├── README.md
├── .gitignore
├── .env / .env.example
├── backend/
│   ├── pyproject.toml
│   ├── server.py
│   ├── src/gemini_brain/
│   ├── tests/
│   ├── sql/
│   ├── scripts/{ops,eval,archive}/
│   └── deploy/
├── frontend/          # canonical UI (Auth0 + blocks)
└── docs/{product,archive}/
```

## Phases

### Phase 1 — UI consolidation (1A)
- Port `ModelHealthModal` (+ wire into App) from `ui/`
- Port `TenantBoundaryCard` if still useful for tenant-gated empty states
- Delete entire `ui/` directory
- Update docs that still reference `ui/`

### Phase 2 — Backend move
- Move `src/`, `tests/`, `sql/`, `scripts/`, `deploy/`, `pyproject.toml`, `server.py` → `backend/`
- Fix `settings.py` `_ENV_FILE` parents depth (repo-root `.env`)
- Update `deploy/provision.sh` + systemd unit `--app-dir` / WorkingDirectory
- Keep root `.env` (shared) or document `backend/.env` symlink — prefer **repo-root `.env`** for one secrets file

### Phase 3 — Deep clean (2C)
- Consolidate `agents/bedrock_client.py` into `reasoning/bedrock_client.py` (agents import from reasoning)
- Remove empty `tenant/` package (or restore minimal stub only if imports break)
- Archive: `measure_baseline/phase1/phase2`, ad-hoc `test_*.py` scripts, `run_demo.py`
- Archive docs: latency/routing phase notes, large audits, PDF
- Product docs stay under `docs/product/`
- Hunt unused modules via import graph + test failures
- Wipe local `*.egg-info`, caches (gitignored)

### Phase 4 — DX
- Restore `.env.example` (no secrets)
- Rewrite root README for new layout
- Tighten `.gitignore` (drop `ui/node_modules` special-case)
- Smoke: `pytest`, `frontend` build, uvicorn `/api/v1/health`

## Explicit non-goals
- Do not merge into `accutax-backend` / `accutax-frontend`
- Do not rename public API paths
- Do not rewrite the orchestrator agent pipeline in this pass (only Bedrock client de-dupe)
