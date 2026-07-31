#!/usr/bin/env bash
# run-demo.sh -- One-command two-agent demo startup
#
# Starts a locally checked-out seller agent on :8001 in the background, waits
# for it to be ready, then launches the buyer agent API on :8000 in the
# foreground. Once both are up you can exercise the cross-agent flow (see
# "Verify" in the README): /media-kit, /products/search, and the booking
# endpoints.
#
# Supported layouts (checked in this order):
#   1. SELLER_DIR=/path/to/seller-agent  environment override
#   2. ../seller-agent                   public side-by-side clone
#   3. ../ad_seller_system               legacy internal layout
#
# Usage:
#   ./run-demo.sh              # start seller + buyer
#   ./run-demo.sh --dry-run    # resolve paths and print the plan, start nothing
#
# Ports (override via environment):
#   SELLER_PORT=8001  BUYER_PORT=8000
#
# Prefer containers? The docker-compose demo path is infra/docker/docker-compose.yml
# (see the "Docker" section of the README).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUYER_DIR="$SCRIPT_DIR"
PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SELLER_PORT="${SELLER_PORT:-8001}"
BUYER_PORT="${BUYER_PORT:-8000}"

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
fi

# ---------------------------------------------------------------------------
# Locate the seller checkout
# ---------------------------------------------------------------------------

is_seller_checkout() {
    # A usable seller checkout has the ad_seller package source.
    [ -d "$1/src/ad_seller" ]
}

SELLER_DIR_SOURCE=""
if [ -n "${SELLER_DIR:-}" ]; then
    # Explicit override: honor it or fail loudly -- never fall back silently.
    if ! is_seller_checkout "$SELLER_DIR"; then
        echo "ERROR: SELLER_DIR is set to '$SELLER_DIR' but that is not a seller-agent checkout" >&2
        echo "  (expected to find src/ad_seller/ inside it)." >&2
        exit 1
    fi
    SELLER_DIR="$(cd "$SELLER_DIR" && pwd)"
    SELLER_DIR_SOURCE="SELLER_DIR environment override"
elif is_seller_checkout "$PARENT_DIR/seller-agent"; then
    SELLER_DIR="$PARENT_DIR/seller-agent"
    SELLER_DIR_SOURCE="side-by-side public clone"
elif is_seller_checkout "$PARENT_DIR/ad_seller_system"; then
    SELLER_DIR="$PARENT_DIR/ad_seller_system"
    SELLER_DIR_SOURCE="legacy internal layout"
else
    cat >&2 <<EOF
ERROR: could not find a seller-agent checkout.

Looked for, in order:
  1. \$SELLER_DIR environment override        (not set)
  2. $PARENT_DIR/seller-agent      (public side-by-side clone)
  3. $PARENT_DIR/ad_seller_system  (legacy internal layout)

Fix -- clone the public seller agent next to this repo:
  git clone https://github.com/IABTechLab/seller-agent.git "$PARENT_DIR/seller-agent"
  cd "$PARENT_DIR/seller-agent" && pip install -e .

or point at an existing checkout:
  SELLER_DIR=/path/to/seller-agent ./run-demo.sh

Prefer containers? Use the docker-compose demo path instead:
  infra/docker/docker-compose.yml  (see the "Docker" section of the README)
EOF
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve a Python for each repo (no hardcoded venv paths)
# ---------------------------------------------------------------------------

find_python() {
    # Prefer the repo's own virtualenv, then whatever python3 is active.
    local dir="$1"
    if [ -x "$dir/.venv/bin/python" ]; then
        echo "$dir/.venv/bin/python"
    elif [ -x "$dir/venv/bin/python" ]; then
        echo "$dir/venv/bin/python"
    else
        command -v python3 || true
    fi
}

BUYER_PYTHON="$(find_python "$BUYER_DIR")"
SELLER_PYTHON="$(find_python "$SELLER_DIR")"

if [ -z "$BUYER_PYTHON" ] || [ -z "$SELLER_PYTHON" ]; then
    echo "ERROR: no python3 found. Install Python 3.11+ or create a venv in each repo:" >&2
    echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Report the plan
# ---------------------------------------------------------------------------

echo "========================================"
echo "Buyer Agent - Two-Agent Demo"
echo "========================================"
echo ""
echo "  Buyer repo:    $BUYER_DIR"
echo "  Seller repo:   $SELLER_DIR ($SELLER_DIR_SOURCE)"
echo "  Buyer python:  $BUYER_PYTHON"
echo "  Seller python: $SELLER_PYTHON"
echo "  Seller port:   $SELLER_PORT"
echo "  Buyer port:    $BUYER_PORT"
echo ""

if [ "$DRY_RUN" = "1" ]; then
    echo "Dry run: paths resolved OK, not starting servers."
    exit 0
fi

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

if ! "$SELLER_PYTHON" -c "import ad_seller" >/dev/null 2>&1; then
    echo "ERROR: '$SELLER_PYTHON' cannot import ad_seller." >&2
    echo "  Fix: cd $SELLER_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

if ! "$BUYER_PYTHON" -c "import ad_buyer" >/dev/null 2>&1; then
    echo "ERROR: '$BUYER_PYTHON' cannot import ad_buyer." >&2
    echo "  Fix: cd $BUYER_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

if command -v lsof >/dev/null 2>&1; then
    for port_info in "$SELLER_PORT:Seller agent" "$BUYER_PORT:Buyer agent"; do
        port="${port_info%%:*}"
        name="${port_info#*:}"
        if lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo "ERROR: port $port already in use ($name). Stop the existing process first:" >&2
            echo "  lsof -Pi :$port -sTCP:LISTEN" >&2
            exit 1
        fi
    done
fi

echo "Preflight checks passed."
echo ""

LOG_DIR="$BUYER_DIR/logs"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# PID tracking for cleanup
# ---------------------------------------------------------------------------

PIDS=()

# shellcheck disable=SC2329  # invoked via the EXIT trap registered below
cleanup() {
    echo ""
    echo "Shutting down demo servers..."
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo "  Stopping PID $pid"
        fi
    done
    # Block until the servers have actually exited so the ports are free by
    # the time we return (wait also reaps them; already-gone PIDs are a no-op).
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do
        wait "$pid" 2>/dev/null || true
    done
    echo "Done. Logs saved in: $LOG_DIR"
}

# Cleanup runs from the EXIT trap so it fires exactly once on every exit path;
# INT/TERM merely translate the signal into an exit (130/143 = 128 + signal).
# Bash defers trap handlers while a foreground command runs and does NOT
# forward signals to children, so both servers are started in the background
# and awaited with `wait` (which IS interruptible by trapped signals) --
# previously a `kill -TERM` at this script was swallowed until the foreground
# buyer exited, leaving both uvicorns running and the ports held.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ---------------------------------------------------------------------------
# Start the seller agent (background)
# ---------------------------------------------------------------------------

echo "Starting seller agent on port $SELLER_PORT..."
(
    cd "$SELLER_DIR"
    exec "$SELLER_PYTHON" -m uvicorn ad_seller.interfaces.api.main:app \
        --port "$SELLER_PORT" >"$LOG_DIR/seller.log" 2>&1
) &
SELLER_PID=$!
PIDS+=("$SELLER_PID")
echo "  Seller agent started (PID: $SELLER_PID), log: $LOG_DIR/seller.log"
echo ""

# ---------------------------------------------------------------------------
# Wait for the seller to be ready
# ---------------------------------------------------------------------------

echo "Waiting for seller agent..."
MAX_WAIT=30
elapsed=0
until curl -fsS "http://localhost:$SELLER_PORT/health" >/dev/null 2>&1; do
    if ! kill -0 "$SELLER_PID" 2>/dev/null; then
        echo "ERROR: seller agent exited during startup. Check $LOG_DIR/seller.log" >&2
        exit 1
    fi
    if [ "$elapsed" -ge "$MAX_WAIT" ]; then
        echo "WARNING: seller not answering /health after ${MAX_WAIT}s; continuing anyway." >&2
        echo "         Check $LOG_DIR/seller.log if cross-agent calls fail." >&2
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done
if [ "$elapsed" -lt "$MAX_WAIT" ]; then
    echo "  Seller agent ready on port $SELLER_PORT"
fi
echo ""

# ---------------------------------------------------------------------------
# Launch the buyer agent (foreground)
# ---------------------------------------------------------------------------

echo "========================================"
echo "Starting buyer agent on port $BUYER_PORT..."
echo "  Try:  curl http://localhost:$BUYER_PORT/health"
echo "        curl http://localhost:$SELLER_PORT/media-kit"
echo "  Ctrl-C stops both agents."
echo "========================================"
echo ""

cd "$BUYER_DIR"
# SELLER_ENDPOINTS is what the buyer actually reads (comma-separated, see
# src/ad_buyer/config/settings.py); SELLER_BASE_URL is kept for backward
# compatibility with older docs that referenced it.
# Started in the background and awaited so INT/TERM reach our traps
# immediately (see the trap comment above); output still goes to this
# terminal.
SELLER_BASE_URL="http://localhost:$SELLER_PORT" \
    SELLER_ENDPOINTS="http://localhost:$SELLER_PORT" \
    "$BUYER_PYTHON" -m uvicorn ad_buyer.interfaces.api.main:app --port "$BUYER_PORT" &
BUYER_PID=$!
PIDS+=("$BUYER_PID")

BUYER_STATUS=0
wait "$BUYER_PID" || BUYER_STATUS=$?
exit "$BUYER_STATUS"
