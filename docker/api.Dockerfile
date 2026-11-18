# Accutax AI backend (FastAPI + uvicorn).
# Build from the repository root:  docker build -f docker/api.Dockerfile -t accutax-ai-api .
#
# The image holds code and libraries only. Secrets and per-server settings come from
# the env file at run time; files the app writes go to /data (a volume); the UAE VAT
# knowledge base is mounted at /kb.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: onnxruntime (VAT KB embeddings) needs OpenMP at run time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# 1. Libraries first, read straight from pyproject.toml, so a code change does not
#    reinstall them. Core + vat_kb + mcp extras (dev/test tools are left out).
COPY backend/pyproject.toml ./pyproject.toml
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; extra = p['optional-dependencies']; print('\n'.join(p['dependencies'] + extra['vat_kb'] + extra['mcp']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && python -m spacy download en_core_web_sm \
 && rm /tmp/requirements.txt

# 2. The code. It runs from source (like the systemd unit's --app-dir), because the
#    settings file finds .env and logs/ by its position inside the repository layout.
COPY backend/server.py ./server.py
COPY backend/src ./src
ENV PYTHONPATH=/app/backend/src

# 3. Writable places all live under /data, so one volume keeps them across restarts
#    and image updates. logs/ and data/ are where the app writes relative to backend/.
RUN useradd --system --uid 10001 --create-home --home-dir /home/accutax accutax \
 && mkdir -p /data/logs /data/state /data/artifacts /data/uploads /data/pending /kb \
 && ln -s /data/logs /app/backend/logs \
 && ln -s /data/state /app/backend/data \
 && chown -R accutax:accutax /data /kb

ENV ARTIFACT_DIR=/data/artifacts \
    UPLOAD_DIR=/data/uploads \
    PENDING_ACTION_DIR=/data/pending \
    API_BIND=0.0.0.0 \
    API_PORT=8000

USER accutax
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('API_PORT', '8000'), timeout=4)" || exit 1

# API_BIND / API_PORT can be changed at run time. Host-network mode on a shared server
# sets API_BIND=127.0.0.1 so the API is reachable only through nginx.
CMD ["sh", "-c", "exec uvicorn gemini_brain.api.app:app --app-dir /app/backend/src --host ${API_BIND} --port ${API_PORT} --proxy-headers --forwarded-allow-ips='*'"]
