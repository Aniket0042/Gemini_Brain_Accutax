# Running Accutax AI with Docker

The AI runs as two containers:

| Container | What it is | Built from |
|---|---|---|
| `accutax-ai-api` | The FastAPI backend (Python 3.11, all libraries, spaCy model) | `docker/api.Dockerfile` |
| `accutax-ai-web` | The React UI served by nginx, which forwards `/api` to the backend | `docker/web.Dockerfile` |

What stays outside the images, per server:

- **The app's env file** (database, Bedrock, Accutax, JWT secrets). It's the same `.env` as today; see `docker/app.env.example`.
- **The VAT knowledge-base folder.** It's mounted read/write at `/kb`.
- **The `ai-data` volume.** It holds logs, generated reports, uploads, drafts and MCP state. It survives restarts and image updates.

Frontend `VITE_*` values are baked in when the web image is built. The VM (legacy sign-in) and EC2 (shared sign-in) therefore each need their own **web** build. The **API** image is the same everywhere.

## Which compose file

- **`docker-compose.host.yml`** is for the VM and EC2 as they are today. Their database tunnel, Cube and Accutax backend listen on `127.0.0.1`, so the containers use the server's own network and the existing `.env` works unchanged.
- **`docker-compose.yml`** is for a fresh server where those services are reached by a real host name or IP.

## 1. Build the images (on a machine with Docker)

```bash
cp docker/deploy.env.example docker/deploy.env   # then edit: VITE_* values for the target server
docker compose -f docker-compose.host.yml --env-file docker/deploy.env build
```

Set `AI_IMAGE_TAG` in `docker/deploy.env` to the git commit (for example `47a2a3f`) so every build has its own label to roll back to.

What a build costs: the first build takes about 5–10 minutes. The API image is about 2 GB, plus 2–3 GB of build cache, which `docker builder prune` clears. Later builds reuse the library layer unless `pyproject.toml` changed.

## 2. Copy the images to the server (no registry needed)

```bash
docker save accutax-ai-api:TAG accutax-ai-web:TAG | gzip > accutax-ai-TAG.tar.gz
scp -P 3434 accutax-ai-TAG.tar.gz root@SERVER:/root/
# on the server:
gunzip -c /root/accutax-ai-TAG.tar.gz | podman load     # VM (Podman)
gunzip -c /root/accutax-ai-TAG.tar.gz | docker load     # EC2 (Docker)
```

Later, a private registry (GHCR, ECR) can replace this step with `docker pull`.

## 3. Start it next to the current install (test)

On the server, in the repository folder (only the compose files and `docker/deploy.env` are used; nothing is built there):

```bash
cp docker/deploy.env.example docker/deploy.env
# edit: AI_IMAGE_TAG, AI_ENV_FILE=/opt/accutax-ai/.env,
#       AI_KB_DIR=/opt/accutax-ai-data/vat_kb_data, free AI_WEB_PORT / AI_API_PORT
ss -tlnp | grep -E ':(8020|8021) '      # must print nothing
```

- **VM (Podman 5, no compose yet):** install the compose tool once with `dnf install -y podman-compose` (from EPEL), or `pip3 install podman-compose`. Then:

  ```bash
  podman-compose -f docker-compose.host.yml --env-file docker/deploy.env up -d
  ```

- **EC2 (Docker):**

  ```bash
  docker compose -f docker-compose.host.yml --env-file docker/deploy.env up -d --no-build
  ```

Check it:

```bash
curl -s http://127.0.0.1:8021/api/v1/health      # API
curl -sI http://127.0.0.1:8020/ | head -1          # UI
```

The current systemd service, nginx and ports aren't touched. During the test, both copies of the AI are running, so expect roughly double the AI's usual RAM until one is stopped.

## 4. Switch over (only after the test passes)

Point the server's nginx at the web container: `proxy_pass http://127.0.0.1:8020;` for `location /`. Then reload nginx and stop the old service:

```bash
nginx -t && systemctl reload nginx
systemctl stop accutax-ai-api && systemctl disable accutax-ai-api
```

**Rollback:** restore the old nginx file, reload nginx, then `systemctl enable --now accutax-ai-api`.

## Updating later

Build with a new `AI_IMAGE_TAG`, copy and load it, change `AI_IMAGE_TAG` in the server's `docker/deploy.env`, then run `up -d` again. To roll back, set the previous tag and run `up -d`.

## Own server (images from CI)

A server that runs only the AI doesn't build anything. On every push to `main`, CI (`.github/workflows/ci.yml`) runs the tests, then pushes three images to GitHub's registry: `ghcr.io/aniket0042/accutax-ai-api`, `accutax-ai-web` (built for the zereco.ai shared sign-in) and `accutax-ai-cube` (Cube with `semantic/` baked in). Each gets a `main` tag and a fixed `sha-<commit>` tag.

```bash
docker login ghcr.io -u <github-user>          # token with read:packages
cp docker/prod.env.example docker/prod.env     # set AI_IMAGE_TAG to a sha- tag, and the paths
docker compose -f docker-compose.prod.yml --env-file docker/prod.env pull
docker compose -f docker-compose.prod.yml --env-file docker/prod.env up -d
```

It needs, on the server: the app's `.env`, Cube's `cube.env` (see `semantic/cube.env.example`), the gateway key file and a VAT knowledge-base folder. The folder can start empty: the API builds the knowledge base itself on first start (it needs outbound internet to the FTA site), then rebuilds it monthly at night (`VAT_KB_REFRESH_DAYS`, `VAT_KB_REFRESH_HOUR_UTC`). The key file and the knowledge-base folder must be owned by uid 10001, the user the API image runs as. The web UI listens on `127.0.0.1:8020` and the API on `127.0.0.1:8021`, for the server's own nginx; Cube is not published.

To update, set a newer `AI_IMAGE_TAG`, then `pull` and `up -d`. To roll back, set the previous tag and `up -d`.

## Things to know

- **AWS credentials:** the container can't see the server's `~/.aws` folder. On EC2 with the host compose file, the instance role works. Anywhere else, put `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the env file.
- **Storage settings:** `ARTIFACT_DIR`, `UPLOAD_DIR` and `PENDING_ACTION_DIR` are fixed by the image to the `/data` volume. Don't set them in the env file.
- **Logs:** use `docker compose logs -f api` (or `podman-compose ... logs -f api`). The app's own JSON-lines logs are in the `ai-data` volume under `logs/`.
- **MCP connector:** the API image includes it. It is not started by these files yet.
