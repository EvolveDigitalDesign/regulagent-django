# Python base image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        postgresql-client \
        tesseract-ocr \
        poppler-utils \
        ghostscript \
        default-jre \
        curl \
        unzip \
        git \
        libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (switch by ENVIRONMENT)
ARG ENVIRONMENT=development
COPY requirements/ /tmp/requirements/
RUN if [ "$ENVIRONMENT" = "production" ]; then \
        pip install -r /tmp/requirements/production.txt; \
    else \
        pip install -r /tmp/requirements/development.txt; \
    fi

# Playwright deps and browsers (best-effort, may be skipped in CI)
RUN python -m playwright install-deps chromium || true

# Copy project
COPY . /app

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Install Playwright browser binaries in user space
RUN python -m playwright install chromium || true

# Entrypoint script (will handle migrations, collectstatic, etc.)
USER root
RUN chmod -R 755 /app
USER appuser
ENTRYPOINT ["/app/docker/entrypoint.sh"]

# Default command (overridden by compose)
CMD ["gunicorn", "ra_config.asgi:application", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "60"]


