# Resume Screener — application container image.
#
# Deployed to AZURE APP SERVICE (Linux, Web App for Containers) — see
# 13-UAT-DEPLOYMENT.md. The Container Apps variant this file was first written for
# remains a documented alternative (§15 there) and needs no change to this file:
# either way the CMD is uvicorn on 8000, never streamlit on 8501.
#
# Build:  az acr build --registry <acr> --image resume-screener:<tag> --file Dockerfile .
#         (or locally: docker build -t resume-screener .)
# Run:    docker run -p 8000:8000 -v <share>:/data -e DATA_ROOT=/data ... resume-screener
#
# Configuration arrives as real environment variables (endpoints, deployment names,
# Key Vault URI). NO secrets are baked in and none are passed as environment
# variables: the API keys are fetched from Key Vault at runtime via the app's
# managed identity, and the image itself is pulled with that same identity
# (AcrPull) — there is no registry credential in the app's configuration.

# --- Stage 1: build the React frontend ---------------------------------------------
FROM node:26-alpine AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Stage 2: the Python app --------------------------------------------------------
FROM python:3.11-slim
# uv pinned to the version that wrote uv.lock, by digest (2026-10-14) — ":latest" made
# every build depend on whatever was published that morning.
COPY --from=ghcr.io/astral-sh/uv:0.11.25@sha256:1e3808aa9023d0980e7c15b1fa7c1ac16ff35925780cf5c459858b2d693f01a9 /uv /uvx /bin/

WORKDIR /app

# Dependency layer first (cached until pyproject/uv.lock change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# The app itself (src layout; .dockerignore keeps data/output/caches/secrets out).
COPY . .
RUN uv sync --frozen --no-dev

# The built SPA, where api.app._WEB_DIST expects it (<repo>/web/dist).
COPY --from=web /build/web/dist ./web/dist

# The commit this image was built from, reported by /api/health so a deployment
# can be confirmed without reading platform logs; ACR passes --build-arg APP_BUILD.
ARG APP_BUILD=""

# The unprivileged user the server runs as (2026-10-14). The image itself starts as
# root only so resume_screener.serve can check that this user can write DATA_ROOT —
# a platform-mounted share whose ownership App Service does not document — and drop
# to it before serving; if it cannot, the server stays root and logs a warning
# instead of failing to start. /data exists for a run without a mount.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home \
            --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data

# All mutable state lives on the mounted Azure Files share (containerapp.patch.yaml
# mounts the "appdata" storage at /data); dataroot.bootstrap() seeds it on first run.
ENV DATA_ROOT=/data \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    APP_BUILD=$APP_BUILD

EXPOSE 8000

# Liveness: the no-auth, no-I/O /api/health probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# uvicorn on 8000 as before, started by resume_screener.serve: as uid 10001 when the
# data root allows it, and without a Server header naming the software.
CMD ["python", "-m", "resume_screener.serve"]
