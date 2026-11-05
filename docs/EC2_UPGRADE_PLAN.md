# EC2 upgrade plan: AccuTax AI + Cube on 13.205.100.89

Status: plan only, 27 Oct 2026. Nothing on EC2 has been changed. The one exception: `git fetch` in
`/opt/gemini-brain` added the `origin/deploy/vm-3535` ref. It changed no files.

## 1. Where EC2 stands today (checked 27 Oct, read-only)

| Item | Now |
|---|---|
| Server | Ubuntu 22.04, 8 CPU, 30 GB RAM, 846 GB free, `ssh -i ~/.ssh/accutax_id_rsa ubuntu@13.205.100.89` (sudo works) |
| AI code | `/opt/gemini-brain`. Branch `main` at `8e268d5` (17 Sep). **153 commits behind** `deploy/vm-3535` (`fb5e2cd`); nothing on EC2 that the branch lacks |
| Local edit | `backend/src/gemini_brain/api/app.py`: `"https://zereco.ai"` added to the CORS list (not committed) |
| Service | `gemini-brain-api.service`, user `geminibrain`, uvicorn `127.0.0.1:8010`, `EnvironmentFile=/opt/gemini-brain/.env` |
| Venv | `/opt/gemini-brain/venv`, Python 3.10 (3.11 is also installed at `/usr/bin/python3.11`) |
| Public edge | nginx site `gemini-brain`: `https://13-205-100-89.sslip.io` serves `/opt/gemini-brain/ui/dist` (old UI) and proxies `/api`, `/docs`, `/redoc`, `/openapi.json` to 8010 |
| Database | `.env` points at `13.127.157.108:5432/accutax_db` as `accutax_user` (direct, no tunnel) |
| Accutax backend | `ACCUTAX_BASE_URL=http://13.127.157.108:8081`, `ACCUTAX_ORG_ID=27` |
| Models | Bedrock `ap-south-1`: Claude 3.5 Sonnet / Claude 3 Haiku, plus a Gemini key |
| Cube | none. Docker 5.4 with `docker compose`; `ubuntu` is in the `docker` group. Port 4000 is free |
| Other services on the box (do not touch) | `accutax-api` :8000, OCR :8001 + worker, model-arena :8003, insight-engine :8100, ollama, vLLM containers, Redis, a local Postgres on 127.0.0.1:5432 |

EC2 database facts that matter:

- The chat tables already have the new layout (`model_arena_chat_sessions.name` exists), so the
  "old tables, drop and recreate" path in `memory/schema.py` does **not** run. There are 5 sessions and 12 messages.
- `organization_ids` is missing on `model_arena_chat_sessions`. `accutax_user` owns the table, so the new code
  **adds it on its first start** (`ALTER TABLE ... ADD COLUMN organization_ids INT[]` plus a backfill `UPDATE`). This
  is a schema change on the shared EC2 DB and needs a yes (step 0).
- The usage tables (`ai_usage_events` and the rest) do not exist. Keep `USAGE_DB` off.
- There is no `cube_reader` role yet. `accutax_user` cannot create roles.

## 2. Approach: side by side, then switch nginx

Install the new code in a **new folder** `/opt/accutax-ai` with its own venv and service (`accutax-ai-api` on
`127.0.0.1:8011`). That is the same layout as on the VM. The old `/opt/gemini-brain` and `gemini-brain-api` on 8010 stay
untouched and running. Going live is changing one `proxy_pass` port in nginx; rolling back is changing it back.

## 3. Steps

### Step 0: decisions and prerequisites (before any change)

- [x] **Yes to the chat-table column** (approved 27 Oct). The new code adds `organization_ids` to
      `model_arena_chat_sessions` on first start.
- [ ] **`cube_reader` on `accutax_db`, created by the EC2 DB superuser** (whoever runs Postgres at 13.127.157.108):
      `semantic/dba/cube_reader_ec2.sql` (all grants in one file, including `retention_releases` for v2).
      Generate the password there; it goes only into `/opt/accutax-ai/semantic/cube.env` on EC2.
- [ ] **Bedrock access.** The new code uses `in.anthropic.claude-sonnet-5` and
      `in.anthropic.claude-haiku-4-5-20251001-v1:0` (the VM values), plus `openai.gpt-oss-120b-1:0` for the VAT KB.
      Confirm that the EC2 AWS keys can call them in `ap-south-1` (one test call each).
- [ ] **Security first.** (a) Stop `pgvector-tunnel.service`: it is in a restart loop and SSHes as `root` into the VM
      with a password from `sshpass`. Change that VM root password. (b) Rotate the `accutax_db` password and limit
      5432 on 13.127.157.108 to the EC2 address. (c) `chmod 600` both `.env` files, which are world-readable today.

### Step 1: back up (nothing changes yet)

```bash
TS=$(date +%Y%m%d-%H%M)
sudo tar czf /root/gemini-brain-pre-upgrade-$TS.tgz -C /opt gemini-brain        # code, .env, venv, ui/dist
sudo cp /etc/nginx/sites-available/gemini-brain /root/nginx-gemini-brain.pre-upgrade-$TS
sudo cp /etc/systemd/system/gemini-brain-api.service /root/
cd /opt/gemini-brain && git diff > /root/gemini-brain-local-cors-$TS.patch
```

Optional and read-only: export the 5 chat sessions and 12 messages with `COPY ... TO STDOUT`.

### Step 2: new code beside the old

```bash
sudo git clone -b deploy/vm-3535 https://github.com/Aniket0042/Gemini_Brain_Accutax.git /opt/accutax-ai
cd /opt/accutax-ai && sudo git log -1 --oneline          # expect fb5e2cd or newer
sudo python3.11 -m venv venv
sudo venv/bin/pip install -e "backend[vat_kb]"
cd frontend && sudo npm ci && sudo npm run build          # optional: only if nginx should serve the new UI
sudo mkdir -p /opt/accutax-ai-data && sudo rsync ...      # copy vat_kb_data from VM /opt/accutax-ai-data/vat_kb_data
sudo chown -R geminibrain:geminibrain /opt/accutax-ai /opt/accutax-ai-data
```

`zereco.ai` CORS: do not carry the code patch over. Put it in `.env` instead (`CORS_ORIGINS`, step 3), so the code stays
the same as on the VM.

### Step 3: `/opt/accutax-ai/.env` (root, 600)

Start from the VM's key list, not from the old EC2 `.env`. The values differ from the VM like this:

| Key | EC2 value |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `13.127.157.108` / `5432` / `accutax_db` / `accutax_user` / (rotated password) |
| `DB_1_5_*` | same as `DB_*` (EC2 has one Accutax DB) |
| `ACCUTAX_BASE_URL` | `http://13.127.157.108:8081` (from the old EC2 `.env`) |
| `ACCUTAX_JWT_SECRET` | the EC2 backend's JWT secret (copy from the old EC2 `.env`), so the zereco.ai handoff works |
| `ACCUTAX_ORG_ID`, `ACCUTAX_USER_ID` | from the old EC2 `.env` (org 27 there; check it exists in `accutax_db`, whose orgs are 1 and 2) |
| `CORS_ORIGINS` | `https://zereco.ai,https://accutax-ai-chat.netlify.app,https://13-205-100-89.sslip.io` |
| `ACCUTAX_APP_URL` | `https://zereco.ai` |
| `CUBE_API_SECRET` | new `openssl rand -hex 32`, the same value as `CUBEJS_API_SECRET` in `cube.env` |
| `BEDROCK_REGION` / `BEDROCK_MODEL_ID` / `BEDROCK_MODEL_ID_FAST` | as on the VM, if step 0 confirmed access |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION` | from the old EC2 `.env` |
| `VAT_KB_ENABLED` / `VAT_KB_DIR` / `VAT_KB_MODEL_ID` | `true` / `/opt/accutax-ai-data/vat_kb_data` / as on the VM |
| `AGENT_MODE` | `primary` (as on the VM) |
| `USAGE_DB` | `off` (no usage tables on EC2) |
| `UPLOADS_ENABLED`, `PII_*`, `METRICS_BACKEND`, `MULTI_ORG_ENABLED` | as on the VM |
| `SHOW_SQL_TRACES` | `false` |

### Step 4: Cube on Docker, `ACCUTAX_SCHEMA=v2`

```bash
cd /opt/accutax-ai/semantic
sudo cp cube.env.example cube.env && sudo chmod 600 cube.env
#   CUBEJS_DB_HOST=13.127.157.108  CUBEJS_DB_NAME=accutax_db  CUBEJS_DB_USER=cube_reader  CUBEJS_DB_PASS=<from DBA>
#   ACCUTAX_SCHEMA=v2               CUBEJS_API_SECRET=<same as CUBE_API_SECRET>
sudo docker compose -f docker-compose.cube.yml up -d       # cube_api on 127.0.0.1:4000 + cubestore
curl -s 127.0.0.1:4000/readyz
```

`docker-compose.cube.yml` already binds only to `127.0.0.1:4000`, uses the same `v1.7.50` images as the VM, and has
`restart: unless-stopped`.

Checks before the AI uses it:

- `compare_compiled.py` against the VM Cube cannot run, because the schemas differ. Instead, for each of org 1 and
  org 2, check receivables, payables, received and paid against the figures in the schema-switch draft (3.4a):
  org 1 receivables 88,858.05, invoice 1136 4,750.
- `cube_golden.py --orgs 1-2` with the EC2 answers as reference (its expected SQL runs straight on `accutax_db`).
- Spot-check a few figures against the zereco.ai dashboard (invoices 1106-1136).

### Step 5: new AI service on 8011 (old one keeps running)

```bash
sudo cp /opt/accutax-ai/backend/deploy/gemini-brain-api.service /etc/systemd/system/accutax-ai-api.service
# edit: WorkingDirectory=/opt/accutax-ai, EnvironmentFile=/opt/accutax-ai/.env,
#       ExecStart=/opt/accutax-ai/venv/bin/uvicorn gemini_brain.api.app:app --host 127.0.0.1 --port 8011 --app-dir /opt/accutax-ai/backend/src
sudo systemctl daemon-reload && sudo systemctl enable --now accutax-ai-api
curl -s 127.0.0.1:8011/api/v1/health
```

The first start adds `organization_ids` (step 0). After that, run a few questions directly against 8011 (an org 1
token) and then the golden set `replay_real_questions.py`, which is paid and so runs once. The VM golden set expects
VM figures, so on EC2 judge it by status/errors and by spot checks against Cube, not by exact totals.

### Step 6: go live

```bash
sudo sed -i 's#127.0.0.1:8010#127.0.0.1:8011#g' /etc/nginx/sites-available/gemini-brain
# optional new UI: root /opt/accutax-ai/frontend/dist;
sudo nginx -t && sudo systemctl reload nginx
```

Netlify `accutax-ai-chat` needs no change, because it already proxies `/api` to `13-205-100-89.sslip.io`. Then:

- check the handoff from zereco.ai: log in there, open the AI;
- check one question in the Netlify app;
- check that `/docs` shows the new API.

Keep `gemini-brain-api` (8010) running for a week as the fallback, then stop and disable it.

## 3a. Progress

27 Oct: steps 1-3 done. The new service is not created, nginx is untouched, and the old service on 8010 is still live.

- Backups in `/root`, all with the suffix `-20261027-ec2upg`:
  - `gemini-brain-pre-upgrade-*.tgz` (600; holds code, `.env`, venv and `ui/dist`);
  - the nginx site, the unit and the CORS patch.
- `/opt/accutax-ai` is on `fb5e2cd`. The venv uses Python **3.11.0rc1**, which is Ubuntu 22.04's `python3.11`; the VM has
  3.11.13. `pip install -e "backend[vat_kb]"` went through and the imports work.
- `/opt/accutax-ai-data/vat_kb_data` was copied from the VM: 127 files, every per-file checksum identical.
- `/opt/accutax-ai/.env` (root, 600) was written from the old EC2 `.env` plus the VM settings:
  - a new `CUBE_API_SECRET` and a new `PII_SNAPSHOT_KEY`;
  - `USAGE_DB` and `USAGE_ENFORCE` off;
  - the admin and preview email lists empty;
  - `ACCUTAX_AUTH_TOKEN` and the service email/password left out (no code reads them).
- Checks with the new `.env`:
  - the settings load;
  - `accutax_db` is reachable read-only;
  - Bedrock Sonnet 5, Haiku 4.5 and gpt-oss-120b all answer with the EC2 AWS keys.
- Open: `ACCUTAX_ORG_ID=27` does not exist in `accutax_db` (orgs 1-4). It is carried over from the old `.env` unchanged.

27 Oct: step 4 done.

- `semantic/cube.env` (root, 600) has `ACCUTAX_SCHEMA=v2` and **temporarily** connects as `accutax_user`. Nobody has the
  `postgres` password, so `cube_reader` could not be created. The `accutax_user` session time zone is already UTC.
- `docker compose -f docker-compose.cube.yml up -d` started `accutax-semantic-cube_api-1` (127.0.0.1:4000) and
  `accutax-semantic-cubestore-1`; `/readyz` returns 200.
- `cube_golden.py --orgs 1-4` with the new `.env`: 13/13 pass.
- Cube against the backend's own figures in SQL, orgs 1-4:
  - receivables match (org 1: 92,714.18 over 21 invoices; org 2: 26,847.50 over 4; invoice 1136 has retention 250);
  - payables match (org 1: 189.00);
  - payments in and out match (org 1: 2,861,567.21 / 2,272,943.90; org 2: 3,695,208.32 / 577,095.75);
  - sales by month match, except one case below.
- **Data finding:** 10 invoices (1115-1124, `INV-2026-1001`..`1010`, org 1, dated Sep 2026, created 23 Oct, 94,035.50
  in total) and 53 expenses have `status_type_id` NULL. Every Cube cube joins `status_type` with an inner join, so these
  documents are missing from sales, receivables, purchases and payables. The VM model does the same, so this is not
  caused by v2. Decide what a missing status means (for example, treat it as issued and open) before changing the
  model. The model stays as it is until the backend team explains it.

27 Oct: step 5 done. nginx was not touched, and the old service on 8010 is still live.

- `/etc/systemd/system/accutax-ai-api.service` is the same as the VM unit (user `geminibrain`, `WorkingDirectory`
  `/opt/accutax-ai/backend`, uvicorn `127.0.0.1:8011`), plus `After=docker.service`. It is enabled and active.
- The app reads `/opt/accutax-ai/.env` itself (`settings.py`), so the file is now owned by `geminibrain`, mode 600,
  as on the VM.
- The `[vat_kb]` extra did not bring in `onnxruntime` or the spaCy model on Python 3.11.0rc1. I installed the VM
  versions by hand: `onnxruntime==1.30.0` and `en_core_web_sm-3.8.0`. Without them the VAT KB warm-up fails and the
  privacy filter finds names by pattern only. Add both to the deploy notes.
- On first start the app added `model_arena_chat_sessions.organization_ids` and filled it for all 6 chats.
- Five questions over HTTP as user 9 (orgs 1, 2, 3, 5), with no `session_id`, so no chat rows were written. All gave
  HTTP 200 with status ok in 2-6 s:
  - receivables 92,714.18 and sales for Sep 83,684.70, both equal to the direct checks;
  - the two-org cash comparison, the VAT question and the top customers.
- Golden set: `tests/data/golden_accounting.json` with the orgs mapped to EC2's (27, 24 → 1; 25 → 2; multi-org →
  1+2) in `/tmp/golden_ec2_orgs12.json`, report in `/tmp/golden_ec2_20261027`.
  - Result: 76/76 status ok, 0 errors, p50 7.4 s, p95 20.0 s, $1.39.
  - Figure screen on 40 cases shared with the VM run of the same day: average share 0.79 on EC2 vs 0.67 on the VM; 24 vs
    23 full hits. These are screens, not grades, and the data differs.
- Netlify `accutax-ai-chat` builds branch `main`, at `ba4f7ac` (17 Sep, 152 commits behind). Step 6 must also move
  Netlify to the new frontend: set its production branch to `deploy/vm-3535`, or fast-forward `main`. Then check Auth0
  login on the Netlify HTTPS origin and the zereco.ai handoff.

27 Oct: step 6 done (live).

- nginx site `gemini-brain`: all 4 `proxy_pass` lines point to `8011`; `nginx -t` passed and nginx was reloaded.
  Backup: `/root/nginx-gemini-brain.pre-step6`. `openapi.json` through sslip shows the new routes (uploads, privacy).
- `git push origin fb5e2cd:refs/heads/main` was a fast-forward (`ba4f7ac..fb5e2cd`, no force); `deploy/vm-3535` is
  unchanged. Netlify built it on its own: deploy `6ae0c143a7db7a85e79c2f31`, commit `fb5e2cd`, ready in 25 s. The old
  deploy for rollback is `6ad8948fa8313569137eba8a`.
- Through Netlify:
  - `/api/v1/health` returns ok;
  - `/api/v1/uploads/config` returns 401 (the route exists);
  - a receivables question as user 9 gave 92,714.18, HTTP 200, 4.5 s;
  - the login page loads.
- The log shows `bank.payee not read ... 'bank' is not queryable` from the privacy dictionary. The VM logs the same
  thing, so it is not new.
- Still to test by a person: an Auth0 login on Netlify and the zereco.ai handoff (`?handoff=1`). Keep
  `gemini-brain-api` (8010) for a week, then stop it.

## 4. Rollback

| Problem | Undo |
|---|---|
| After step 6 | Put `8010` back in the nginx site (or copy the `/root/nginx-gemini-brain.pre-upgrade-*` backup) and run `nginx -t && systemctl reload nginx`. Takes seconds; the old service never stopped |
| New service | `systemctl disable --now accutax-ai-api`; `rm -rf /opt/accutax-ai` if wanted |
| Cube | `docker compose -f docker-compose.cube.yml down` (add `-v` to drop the cubestore volume) |
| `organization_ids` column | Leave it: the old code ignores unknown columns. If it must go: `ALTER TABLE public.model_arena_chat_sessions DROP COLUMN organization_ids;` (needs a yes) |
| `cube_reader` | The revoke + `DROP ROLE` block at the end of `semantic/dba/cube_reader.sql` |
| Old folder damaged | `tar xzf /root/gemini-brain-pre-upgrade-*.tgz -C /opt` |

## 5. What this does not cover

- The VM is untouched by every step above.
- v2 payments for foreign currency and supplier refunds are written but have no EC2 rows to test against yet.
- The optional v2 dimensions (currency, retention fields) from the schema-switch draft, section 3.5.
- Upgrading the VM backend to the Calamity010 version.
