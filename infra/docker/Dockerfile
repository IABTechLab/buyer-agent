# =============================================================================
# Ad Buyer System — Multi-stage Dockerfile
# =============================================================================
# Stage 1: Build dependencies (cached layer)
# Stage 2: Production runtime (minimal image)
#
# Private git deps (iab-agentic-primitives) require a GitHub token.
# Pass it at build time via BuildKit secret — never baked into any layer:
#
#   docker build \
#     --secret id=github_token,env=GITHUB_TOKEN \
#     -f infra/docker/Dockerfile -t buyer-agent:local .
#
# In GitHub Actions the GITHUB_TOKEN is passed automatically (see deploy.yml).
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into a virtual env so we can copy it cleanly
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip

# Copy source and install all deps.
# The GitHub token is mounted as a BuildKit secret — used only during this
# RUN step and never written to any image layer.
COPY pyproject.toml README.md ./
COPY src ./src

RUN --mount=type=secret,id=github_token \
    export GH_TOKEN=$(cat /run/secrets/github_token) && \
    GIT_CONFIG_COUNT=1 \
    GIT_CONFIG_KEY_0="url.https://${GH_TOKEN}@github.com/.insteadOf" \
    GIT_CONFIG_VALUE_0="https://github.com/" \
    pip install --no-cache-dir -e .

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

# Register the editable package — deps already in venv, skip resolution
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
