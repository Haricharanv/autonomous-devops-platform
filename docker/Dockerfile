# ==============================================================================
# Stage 1: Builder Stage
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create virtual environment for dependency isolation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install application dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type f -name "*.pyc" -delete

# ==============================================================================
# Stage 2: Production Stage
# ==============================================================================
FROM python:3.11-slim AS production

# Image Metadata Labels
LABEL maintainer="Intelligent DevOps Team <devops@platform.local>" \
      version="1.0.0" \
      description="Intelligent Autonomous DevOps Deployment Platform"

# Set runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=5000

# Install curl for container health check and clean package lists
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create non-root system user and group
RUN groupadd -g 10001 devops && \
    useradd -u 10001 -g devops -s /bin/sh -d /app -M devops

WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder --chown=devops:devops /opt/venv /opt/venv

# Copy only needed application code
COPY --chown=devops:devops . /app/

# Remove tests and cache from production image if any were copied
RUN rm -rf /app/tests /app/__pycache__

# Switch to non-root user
USER devops

# Expose platform port
EXPOSE 5000

# Container Healthcheck using /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run with Gunicorn (4 workers, bind 0.0.0.0:5000)
CMD ["gunicorn", "--workers=4", "--bind=0.0.0.0:5000", "wsgi:app"]
