FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY examples/ ./examples/

# Install Python dependencies
RUN pip install --no-cache-dir -e .
RUN pip install --no-cache-dir rich httpx

# Default command (can be overridden in docker-compose)
CMD ["python", "examples/buyer_demo.py"]
