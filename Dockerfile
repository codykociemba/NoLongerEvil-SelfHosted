# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy project files needed for installation
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY src/ ./src/

# Create data directory
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV SQLITE3_DB_PATH=/app/data/database.sqlite
ENV DEBUG_LOGS_DIR=/app/data/debug-logs

# Default port
ENV PORT=8080

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run with Gunicorn using uvicorn workers
# Workers default to 2*CPU+1 via WORKERS env var
CMD ["sh", "-c", "gunicorn 'nolongerevil.main:create_app()' -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8080} -w ${WORKERS:-$(python -c 'import os; print(min(2 * (os.cpu_count() or 1) + 1, 8))')}"]
