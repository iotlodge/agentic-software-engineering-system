# Control plane + Python workload. git is required: the control plane's
# workspace manager drives real worktrees, branches and merges.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8787/api/metrics || exit 1

CMD ["uv", "run", "ase", "serve", "--host", "0.0.0.0", "--port", "8787"]
