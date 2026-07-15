#!/usr/bin/env bash
# =============================================================================
# Quick test runner for the buyer agent
# =============================================================================
# Sets up PYTHONPATH, activates venv, and runs pytest with any args passed.
#
# Usage:
#   ./test.sh                                    # run all unit tests
#   ./test.sh tests/unit/test_routing_mode.py    # run specific test
#   ./test.sh tests/integration/ -v              # verbose
#   ./test.sh --all                              # run everything
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
export AWS_PROFILE="${AWS_PROFILE:-genai}"
export AWS_REGION="${AWS_REGION:-us-west-2}"

# Activate venv if not already active
if [[ -z "${VIRTUAL_ENV:-}" ]] && [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
  source "${REPO_ROOT}/.venv/bin/activate"
fi

# Default: run unit tests
if [[ $# -eq 0 ]]; then
  echo "Running: pytest tests/unit/ -v"
  exec pytest tests/unit/ -v
elif [[ "$1" == "--all" ]]; then
  shift
  echo "Running: pytest tests/ -v $*"
  exec pytest tests/ -v "$@"
else
  echo "Running: pytest $*"
  exec pytest "$@"
fi
