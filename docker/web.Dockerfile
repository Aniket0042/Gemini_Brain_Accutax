# Accutax AI frontend: builds the React UI, then serves it with nginx.
# Build from the repository root:  docker build -f docker/web.Dockerfile -t accutax-ai-web .
#
# Vite bakes VITE_* values into the JavaScript at BUILD time, so they are build
# arguments here, not run-time settings. A different environment (VM legacy sign-in
# vs EC2 shared sign-in) needs its own web image build; the API image is the same.

# ---- Stage 1: build the UI ------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /ui

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./

ARG VITE_AUTH_MODE=legacy
ARG VITE_BFF_API_URL=
ARG VITE_HUB_URL=
ARG VITE_ACCUTAX_APP_URL=
ARG VITE_SHOW_SQL_TRACES=
ARG VITE_SHOW_API_TRACES=
ARG VITE_SHOW_LLM_TRACES=
ARG VITE_SHOW_PRIVACY_BADGE=
ARG ALLOW_INSECURE_BFF=
ENV VITE_AUTH_MODE=$VITE_AUTH_MODE \
    VITE_BFF_API_URL=$VITE_BFF_API_URL \
    VITE_HUB_URL=$VITE_HUB_URL \
    VITE_ACCUTAX_APP_URL=$VITE_ACCUTAX_APP_URL \
    VITE_SHOW_SQL_TRACES=$VITE_SHOW_SQL_TRACES \
    VITE_SHOW_API_TRACES=$VITE_SHOW_API_TRACES \
    VITE_SHOW_LLM_TRACES=$VITE_SHOW_LLM_TRACES \
    VITE_SHOW_PRIVACY_BADGE=$VITE_SHOW_PRIVACY_BADGE \
    ALLOW_INSECURE_BFF=$ALLOW_INSECURE_BFF

RUN npm run build

# ---- Stage 2: serve it ------------------------------------------------------------
FROM nginx:1.27-alpine

COPY --from=build /ui/dist /usr/share/nginx/html
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template

# Where nginx finds the API and which port it listens on. The defaults suit the
# normal compose network; host-network mode overrides both (see docker-compose.host.yml).
ENV API_UPSTREAM=api:8000 \
    WEB_LISTEN_PORT=80

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:${WEB_LISTEN_PORT}/ || exit 1
