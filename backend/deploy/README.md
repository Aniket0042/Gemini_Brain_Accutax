# backend/deploy: legacy

These files describe the **old shared-VM deployment** (a single EC2 box at `/opt/gemini-brain`, uvicorn on port **8010**, an nginx site on an sslip.io hostname, an ad hoc SSH tunnel to a database box). They are kept for reference only. Do not use them to deploy.

The authoritative setup is [`docs/AI_SERVER_SETUP.md`](../../../docs/AI_SERVER_SETUP.md) in the Accutax workspace: gateway authentication (`AI_AUTH_MODE=gateway`), uvicorn on `127.0.0.1:8000` behind nginx at `ai-internal.zereco.ai`, configuration read from `accutax-ai/.env` (repository root). Production itself runs on ECS (see `accutax-infra`).

| File | Status |
|---|---|
| `gemini-brain-api.service` | Legacy. Port 8010, `/opt/gemini-brain`, `EnvironmentFile=/opt/gemini-brain/.env`. |
| `nginx-gemini-brain.conf` | Legacy. Serves the UI and proxies `/api` to 8010 on one host. The current UI is a separate static site and the API is private. |
| `provision.sh` | Legacy. Provisions the old VM, expects a `.env.example` and mentions `GEMINI_API_KEY`, which the code no longer reads. |
| `gemini-brain-db-tunnel.service` | Legacy. SSH tunnel to the old database box. |
| `vat-kb-refresh.service`, `vat-kb-refresh.timer` | Still describe a real job (monthly VAT knowledge-base refresh via `scripts/vat_kb_refresh.py`), but the paths (`/opt/accutax-ai`, user `geminibrain`) are those of the old VM. Adapt them to the current server before use. |
