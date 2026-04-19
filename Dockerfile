FROM python:3.11-slim

WORKDIR /app

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency files first (layer cache)
COPY pyproject.toml uv.lock README.md ./

# Install production deps only
RUN uv sync --frozen --no-dev

# Copy application source
COPY backend/ ./backend/
COPY alembic/ ./alembic/
COPY alembic.ini ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
