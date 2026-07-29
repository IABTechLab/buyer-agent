# =============================================================================
# Ad Buyer System — Multi-stage Dockerfile
# =============================================================================
# Stage 1: Build dependencies (cached layer)
# Stage 2: Production runtime (minimal image)
#
# pyproject declares a git dependency (iab-agentic-primitives), which is a
# public repository — no token or BuildKit secret is needed for the clone:
#
#   docker build -f infra/docker/Dockerfile -t buyer-agent:local .
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# git is required: pyproject declares a git dependency (iab-agentic-primitives)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install everything into a virtual env so we can copy it cleanly.
# No separate dependency layer: an editable hatchling build needs the
# source tree present, so a pyproject-only install cannot succeed and
# would only mask failures.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY . .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

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

# Copy virtual env from builder (all deps already resolved, no re-clone needed)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
WORKDIR /app
COPY --from=builder /build/src ./src
COPY --from=builder /build/pyproject.toml ./
COPY --from=builder /build/README.md ./

# Re-point the editable install at /app; dependencies already live in the
# copied venv, so skip resolution (which would also need git at runtime)
RUN pip install --no-cache-dir --no-deps -e .

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
