# =============================================================================
# Ad Buyer System — Multi-stage Dockerfile
# =============================================================================
# Stage 1: Build dependencies (cached layer)
# Stage 2: Production runtime (minimal image)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency metadata first (cache-friendly)
COPY pyproject.toml ./

# Install dependencies into a virtual env so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e . 2>/dev/null; true

# Now copy source and install the package itself
COPY . .
RUN pip install --no-cache-dir -e .

# ---------------------------------------------------------------------------
# Stage 2 — Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 buyer && \
    useradd --uid 1000 --gid buyer --create-home buyer

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
WORKDIR /app
COPY --from=builder /build/src ./src
COPY --from=builder /build/pyproject.toml ./
COPY --from=builder /build/README.md ./

# Install the package in the runtime venv
RUN pip install --no-cache-dir -e .

# Create data directory for SQLite (owned by non-root user)
RUN mkdir -p /app/data && chown buyer:buyer /app/data

# Switch to non-root user
USER buyer

# Default environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:///./data/ad_buyer.db

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "ad_buyer.interfaces.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "2", \
     "--access-log"]
