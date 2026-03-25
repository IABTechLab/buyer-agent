#!/bin/bash
# run-demo.sh -- One-command Campaign Automation demo startup
#
# Starts seller servers (publisher on :8001, DSP on :8002) in the background,
# waits for them to be ready, then launches the Campaign Demo on :5055 in the
# foreground. Opens the browser automatically on macOS.
#
# Usage:
#   ./run-demo.sh
#
# bead: buyer-bqz

set -euo pipefail

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUYER_DIR="$SCRIPT_DIR"
SELLER_DIR="$(cd "$SCRIPT_DIR/../ad_seller_system" && pwd)"

LOG_DIR="$BUYER_DIR/logs"
mkdir -p "$LOG_DIR"

BUYER_PYTHON="$BUYER_DIR/venv/bin/python"
SELLER_PYTHON="$SELLER_DIR/venv/bin/python"

# ---------------------------------------------------------------------------
# PID tracking for cleanup
# ---------------------------------------------------------------------------

PIDS=()

cleanup() {
    echo ""
    echo "========================================"
    echo "Shutting down demo servers..."
    echo "========================================"
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo "  Stopped PID $pid"
        fi
    done
    echo "Done. Logs saved in: $LOG_DIR"
}

trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

echo "========================================"
echo "Ad Buyer System - Campaign Demo"
echo "========================================"
echo ""

# Check venvs
if [ ! -f "$BUYER_PYTHON" ]; then
    echo "ERROR: ad_buyer_system venv not found at $BUYER_DIR/venv"
    echo "  Fix: cd $BUYER_DIR && python3.13 -m venv venv && source venv/bin/activate && pip install -e ."
    exit 1
fi

if [ ! -f "$SELLER_PYTHON" ]; then
    echo "ERROR: ad_seller_system venv not found at $SELLER_DIR/venv"
    echo "  Fix: cd $SELLER_DIR && python3.13 -m venv venv && source venv/bin/activate && pip install -e ."
    exit 1
fi

# Check port conflicts
for port_info in "8001:Publisher" "8002:DSP" "5055:Campaign Demo"; do
    port="${port_info%%:*}"
    name="${port_info##*:}"
    if lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo "ERROR: Port $port already in use ($name). Stop the existing process first:"
        echo "  lsof -Pi :$port -sTCP:LISTEN"
        exit 1
    fi
done

echo "Preflight checks passed."
echo ""

# ---------------------------------------------------------------------------
# Start seller servers (background)
# ---------------------------------------------------------------------------

echo "Starting seller servers..."

cd "$SELLER_DIR/examples"
$SELLER_PYTHON publisher_gam_server.py > "$LOG_DIR/publisher.log" 2>&1 &
PUBLISHER_PID=$!
PIDS+=("$PUBLISHER_PID")
echo "  Publisher/SSP started (PID: $PUBLISHER_PID) on port 8001"

$SELLER_PYTHON dsp_server.py > "$LOG_DIR/dsp.log" 2>&1 &
DSP_PID=$!
PIDS+=("$DSP_PID")
echo "  DSP started (PID: $DSP_PID) on port 8002"

# ---------------------------------------------------------------------------
# Wait for seller servers to be ready
# ---------------------------------------------------------------------------

echo ""
echo "Waiting for seller servers..."

MAX_WAIT=15
for port_info in "8001:Publisher" "8002:DSP"; do
    port="${port_info%%:*}"
    name="${port_info##*:}"
    elapsed=0
    while ! lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1; do
        if [ "$elapsed" -ge "$MAX_WAIT" ]; then
            echo "WARNING: $name (port $port) not ready after ${MAX_WAIT}s. Check $LOG_DIR/"
            break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    if lsof -Pi ":$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo "  $name ready on port $port"
    fi
done

echo ""

# ---------------------------------------------------------------------------
# Launch Campaign Demo (foreground)
# ---------------------------------------------------------------------------

echo "========================================"
echo "Starting Campaign Demo on port 5055..."
echo "========================================"
echo ""

# Open browser after a short delay (macOS only)
if command -v open >/dev/null 2>&1; then
    (sleep 2 && open "http://localhost:5055") &
    PIDS+=("$!")
fi

cd "$BUYER_DIR"
$BUYER_PYTHON -m ad_buyer.demo.campaign_demo 2>&1 | tee "$LOG_DIR/campaign_demo.log"
