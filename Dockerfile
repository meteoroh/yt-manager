FROM ghcr.io/astral-sh/uv:latest AS uv_bin

FROM python:3.12-slim-bookworm

# Copy uv binary from official image
COPY --from=uv_bin /uv /uvx /bin/

WORKDIR /app

# Enable bytecode compilation and unbuffered stdout
ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy project definition first for dependency layer caching
COPY pyproject.toml uv.lock* README.md ./

# Install production dependencies directly into system python
RUN uv pip install --system --no-cache -e .

# Copy application source and tools
COPY src/ ./src/
COPY tools/ ./tools/

# Install package itself in editable/system mode
RUN uv pip install --system --no-cache --no-deps -e .

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1

CMD ["uvicorn", "yt_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
