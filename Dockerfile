# Iron Jarvis daemon / API (FastAPI + uv).
#
# Build:   docker build -t iron-jarvis .
# Run:     docker run -p 8787:8787 -v ./data:/data -e IRONJARVIS_TOKEN=... iron-jarvis
#
# Note: git and docker are intentionally absent in this image. That's fine —
# the daemon defaults to the native sandbox + non-git-native sessions, which
# work without either. Set IRONJARVIS_TOKEN to require bearer-token auth.
FROM python:3.12-slim

# uv: copy the static binary from the official image (no pip bootstrap needed).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IRONJARVIS_ROOT=/data

WORKDIR /app

# 1) Install dependencies first (cached unless the lockfile changes).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Copy the source and install the project itself.
COPY src ./src
RUN uv sync --frozen --no-dev

# Runtime state (config, sqlite db, workspaces, artifacts) lives under /data.
VOLUME /data
EXPOSE 8787

CMD ["uv", "run", "ironjarvis", "serve", "--host", "0.0.0.0", "--port", "8787", "--root", "/data"]
