FROM ghcr.io/astral-sh/uv:latest AS uv_bin

FROM python:3.12-slim-bookworm

# Copy uv binary from official image
COPY --from=uv_bin /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation and unbuffered stdout
ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy project files and source
COPY pyproject.toml uv.lock* README.md ./
COPY src/ ./src/
COPY tools/ ./tools/

# Install dependencies and project into system python
RUN uv pip install --system --no-cache .

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["uvicorn", "yt_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
