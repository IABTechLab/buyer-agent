#!/usr/bin/env bash
# =============================================================================
# AgentCore runtime test runner for Buyer agent
# =============================================================================
# Usage:
#   bash tests/integration/agentcore/run_tests.sh --profile genai
#   bash tests/integration/agentcore/run_tests.sh --profile genai -k "negotiate"
#   bash tests/integration/agentcore/run_tests.sh --profile genai -k "plan"
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
TEST_FILE="${SCRIPT_DIR}/test_runtime.py"

PYTEST_ARGS=()
PROFILE=""
RUNTIME_ARN=""
AGENT_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)    PROFILE="$2"; shift 2 ;;
    --runtime-arn) RUNTIME_ARN="$2"; shift 2 ;;
    --agent-name) AGENT_NAME="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $(basename "$0") [--profile PROFILE] [--runtime-arn ARN] [-k EXPR] [-v]"
      echo ""
      echo "Test groups (use -k to select):"
      echo "  chat              Chat mode tests"
      echo "  plan              Campaign planning"
      echo "  query_seller      Seller inventory queries"
      echo "  negotiate         Deal negotiation"
      echo "  approve           Deal booking/approval"
      exit 0
      ;;
    *)            PYTEST_ARGS+=("$1"); shift ;;
  esac
done

# Resolve Python — prefer .venv if available
if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON="python3"
fi

CMD=("${PYTHON}" -m pytest "${TEST_FILE}")
[[ -n "${PROFILE}" ]] && CMD+=(--profile "${PROFILE}")
[[ -n "${RUNTIME_ARN}" ]] && CMD+=(--runtime-arn "${RUNTIME_ARN}")
[[ -n "${AGENT_NAME}" ]] && CMD+=(--agent-name "${AGENT_NAME}")

if [[ ! " ${PYTEST_ARGS[*]:-} " =~ " -v " ]] && [[ ! " ${PYTEST_ARGS[*]:-} " =~ " --verbose " ]]; then
  CMD+=(-v)
fi
CMD+=("${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")

echo "============================================="
echo "  Buyer Runtime — AgentCore Tests"
echo "============================================="
echo "  Command: ${CMD[*]}"
echo "============================================="

exec "${CMD[@]}"
