# syntax=docker/dockerfile:1
# One image is the whole site: the API, the database migration CLI, and the built
# single-page app, which the API serves. Build from the repository root:
#   docker build -t novel-engine:<commit> .

# ---- frontend build ---------------------------------------------------------
FROM node:24-bookworm-slim AS web
WORKDIR /src/frontend
RUN corepack enable && corepack prepare pnpm@11.26.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# The site is built without its source maps; they are set aside for error reporting.
RUN pnpm run build \
    && mkdir -p /maps/assets \
    && cp dist/assets/*.js /maps/assets/ \
    && find dist -name '*.map' -exec mv {} /maps/assets/ \;

# ---- source maps, for upload to error reporting only -------------------------
# Not part of the site. Export with:
#   docker build --target web-maps --output type=local,dest=<dir> .
FROM scratch AS web-maps
COPY --from=web /maps/ /

# ---- locked Python dependencies (no dev tools, no optional embeddings) --------
FROM python:3.13-slim-bookworm AS python-deps
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

# ---- runtime ----------------------------------------------------------------
FROM python:3.13-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST_DIR=/app/frontend-dist \
    OBJECT_STORE_DIR=/var/lib/novel-engine/objects
# uid/gid 1000 so file-mounted secrets kept owner-only (0400) on the host are readable.
RUN groupadd --gid 1000 novel \
    && useradd --uid 1000 --gid novel --home-dir /app --shell /usr/sbin/nologin novel \
    && mkdir -p /var/lib/novel-engine/objects \
    && chown -R novel:novel /var/lib/novel-engine
WORKDIR /app
COPY --from=python-deps /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY backend ./backend
COPY --from=web /src/frontend/dist ./frontend-dist
USER novel
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).status == 200 else 1)"]
# Forwarded headers are honoured only from the reverse proxy's network; set
# FORWARDED_ALLOW_IPS to that subnet in the deployment.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
