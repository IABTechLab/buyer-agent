#!/usr/bin/env bash
# =============================================================================
# Ad Buyer System — AgentCore CLI Deploy Script
# =============================================================================
# Deploys the buyer agent to Amazon Bedrock AgentCore using the agentcore CLI.
# Must run from repo root.
#
# Usage:
#   bash infra/aws/agentcore/deploy.sh --profile genai
#   bash infra/aws/agentcore/deploy.sh --profile genai --test
#   bash infra/aws/agentcore/deploy.sh --profile genai --test-only
#   bash infra/aws/agentcore/deploy.sh --profile genai --seller-url ARN
#   bash infra/aws/agentcore/deploy.sh --mode http --name a4a_aamp_buyer_omixaj --profile genai --test
#
# Options:
#   --region REGION     AWS region (default: us-west-2)
#   --name NAME         AgentCore runtime name (default: staging_aamp_buyer)
#   --mode MODE         Deployment mode: http (default: http, only http supported for now)
#   --profile PROFILE   AWS CLI profile
#   --seller-url URL    Seller agent runtime ARN for agent-to-agent communication
#   --test              Deploy then invoke + check CloudWatch logs
#   --test-only         Skip deploy, just invoke + check logs
#   --prompt JSON       Custom invoke payload (default: {"prompt": "hello"})
# =============================================================================

set -euo pipefail

# Load a gitignored .env at repo root if present, so local AWS credentials
# (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN or AWS_PROFILE)
# and the Bedrock endpoint config below can be supplied without exporting them
# by hand. .env is listed in .gitignore and must never be committed.
_SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT_EARLY="$(cd "${_SCRIPT_DIR_EARLY}/../../.." && pwd)"
if [[ -f "${_REPO_ROOT_EARLY}/.env" ]]; then
  echo ">>> Loading environment from ${_REPO_ROOT_EARLY}/.env"
  set -a
  # shellcheck disable=SC1091
  source "${_REPO_ROOT_EARLY}/.env"
  set +a
fi

REGION="${AWS_REGION:-us-west-2}"
AGENT_NAME="${AGENT_NAME:-}"
AWS_PROFILE="${AWS_PROFILE:-}"
# Default model: current-generation Claude on Bedrock via the Anthropic
# Messages endpoint (see ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL below). This
# replaces the retired Amazon Nova Pro default. The model id is the Bedrock
# model / inference-profile id passed to the Anthropic provider.
LLM_MODEL="${DEFAULT_LLM_MODEL:-us.anthropic.claude-sonnet-5}"
MEMORY_MODEL="${MEMORY_LLM_MODEL:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"
# Bedrock's Anthropic-compatible (Messages API) base URL + API key. Setting
# these routes Claude through CrewAI's native Anthropic provider against
# Bedrock, so the Converse toolUse/toolResult sanitizer is not applied.
ANTHROPIC_BASE_URL="${ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL:-https://bedrock-runtime.${REGION}.amazonaws.com/anthropic}"
BEDROCK_API_KEY="${ANTHROPIC_COMPATIBLE_LLM_API_KEY:-${AWS_BEARER_TOKEN_BEDROCK:-}}"
SELLER_AGENT_URL="${SELLER_AGENT_URL:-}"
DEPLOY_MODE="http"
DO_TEST=false
TEST_ONLY=false
DO_CLEANUP=false
PROMPT='{"prompt": "What campaigns are active?", "routing_mode": "crew"}'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)     REGION="$2"; shift 2 ;;
    --name)       AGENT_NAME="$2"; shift 2 ;;
    --mode)       DEPLOY_MODE="$2"; shift 2 ;;
    --profile)    AWS_PROFILE="$2"; shift 2 ;;
    --seller-url) SELLER_AGENT_URL="$2"; shift 2 ;;
    --test)       DO_TEST=true; shift ;;
    --test-only)  TEST_ONLY=true; DO_TEST=true; shift ;;
    --cleanup)    DO_CLEANUP=true; shift ;;
    --prompt)     PROMPT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--region REGION] [--name NAME] [--mode MODE] [--profile PROFILE] [--seller-url URL] [--test] [--test-only] [--cleanup] [--prompt JSON]"
      exit 0 ;;
    *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Validate deploy mode (only http supported for now — MCP causes OOM, Issue 19)
if [[ "${DEPLOY_MODE}" != "http" ]]; then
  echo "ERROR: Only --mode http is supported for buyer agent (MCP deferred — Issue 19)"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Resolve agent name from .bedrock_agentcore.yaml if not provided
if [[ -z "${AGENT_NAME}" ]]; then
  if [[ -f "${REPO_ROOT}/.bedrock_agentcore.yaml" ]]; then
    AGENT_NAME=$(grep "^default_agent:" "${REPO_ROOT}/.bedrock_agentcore.yaml" | awk '{print $2}')
  fi
  AGENT_NAME="${AGENT_NAME:-staging_aamp_buyer}"
fi

if [[ -n "${AWS_PROFILE}" ]]; then
  export AWS_PROFILE
fi

cd "${REPO_ROOT}"

# ── Cleanup (--cleanup) ────────────────────────────────────────────
if [[ "${DO_CLEANUP}" == "true" ]]; then
  echo "============================================="
  echo "  Cleanup: Destroying Buyer AgentCore Runtime"
  echo "============================================="
  echo "  Agent : ${AGENT_NAME}"
  echo "  Region: ${REGION}"
  echo "============================================="

  # Set this agent as default in yaml (without reconfiguring)
  if [[ -f .bedrock_agentcore.yaml ]]; then
    sed -i.bak "s/^default_agent:.*/default_agent: ${AGENT_NAME}/" .bedrock_agentcore.yaml
    rm -f .bedrock_agentcore.yaml.bak
  fi

  agentcore destroy --force --delete-ecr-repo 2>&1 || echo "  ⚠️  destroy failed or agent not found: ${AGENT_NAME}"

  echo ""
  echo "============================================="
  echo "  ✅ Cleanup Complete"
  echo "============================================="
  exit 0
fi

# ── Deploy ──────────────────────────────────────────────────────────
if [[ "${TEST_ONLY}" == "false" ]]; then
  echo "============================================="
  echo "  Ad Buyer Agent — AgentCore CLI Deploy"
  echo "============================================="
  echo "  Agent Name  : ${AGENT_NAME}"
  echo "  Region      : ${REGION}"
  echo "  LLM Model   : ${LLM_MODEL}"
  echo "  Seller URL  : ${SELLER_AGENT_URL:-<not set>}"
  [[ -n "${AWS_PROFILE}" ]] && echo "  AWS Profile : ${AWS_PROFILE}"
  echo "============================================="

  # Ensure CLI is installed
  if ! command -v agentcore &>/dev/null; then
    echo ">>> Installing agentcore CLI..."
    pip install bedrock-agentcore-starter-toolkit==0.3.4
  fi

  # ── Workaround: Hide root Dockerfile during configure/deploy ──────
  # The agentcore toolkit copies a root-level Dockerfile into the build
  # directory if one exists, ignoring the -e entrypoint flag. The community
  # repo ships its own Dockerfile for local docker-compose usage, so we
  # temporarily move it aside to let the toolkit generate the correct one
  # from its template with our HTTP entrypoint.
  _ROOT_DOCKERFILE="${REPO_ROOT}/Dockerfile"
  _DOCKERFILE_HIDDEN=false
  # Trap to restore Dockerfile on any exit (success or failure)
  restore_dockerfile() { [[ "${_DOCKERFILE_HIDDEN}" == "true" ]] && mv "${_ROOT_DOCKERFILE}.community-bak" "${_ROOT_DOCKERFILE}" 2>/dev/null; }
  trap restore_dockerfile EXIT
  if [[ -f "${_ROOT_DOCKERFILE}" ]]; then
    echo "  ⚠️  Root Dockerfile detected — hiding during deploy (toolkit would override entrypoint)"
    mv "${_ROOT_DOCKERFILE}" "${_ROOT_DOCKERFILE}.community-bak"
    _DOCKERFILE_HIDDEN=true
  fi

  # Also remove the stale generated Dockerfile so the toolkit regenerates it
  _GENERATED_DOCKERFILE="${REPO_ROOT}/.bedrock_agentcore/${AGENT_NAME}/Dockerfile"
  if [[ -f "${_GENERATED_DOCKERFILE}" ]]; then
    echo "  🗑️  Removing stale generated Dockerfile: ${_GENERATED_DOCKERFILE}"
    rm -f "${_GENERATED_DOCKERFILE}"
  fi

  # ── Auth stack (opt-in): provision the buyer runtime execution role with a
  # scoped grant to invoke the seller runtimes, and pass it to agentcore
  # configure. Only when SELLER_AGENT_URL is an AgentCore ARN (buyer->seller
  # over InvokeAgentRuntime). For a plain HTTP seller URL or no seller, the CLI
  # auto-creates the default role as before. See auth-agentcore.yaml for the
  # security model (same-account / trusted cross-account only; cross-org uses
  # the gateway + CUSTOM_JWT path).
  EXECUTION_ROLE_ARN=""
  if [[ "${SELLER_AGENT_URL}" == arn:* ]]; then
    AUTH_STACK="aamp-buyer-auth-${ENVIRONMENT:-staging}"
    echo ""
    echo ">>> Deploying auth stack (buyer runtime role + seller-invoke): ${AUTH_STACK}"
    aws cloudformation deploy \
      --stack-name "${AUTH_STACK}" \
      --template-file infra/aws/agentcore/auth-agentcore.yaml \
      --capabilities CAPABILITY_NAMED_IAM \
      --parameter-overrides "Environment=${ENVIRONMENT:-staging}" \
      --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"}
    EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
      --stack-name "${AUTH_STACK}" --region "${REGION}" ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} \
      --query "Stacks[0].Outputs[?OutputKey=='BuyerRuntimeExecutionRoleArn'].OutputValue" \
      --output text 2>/dev/null || true)
    echo "  execution role: ${EXECUTION_ROLE_ARN:-<none>}"
  fi

  # Configure
  echo ""
  echo ">>> Configuring agent..."
  agentcore configure \
    -e src/ad_buyer/interfaces/agentcore/http_main.py \
    -n "${AGENT_NAME}" \
    -rf infra/aws/agentcore/requirements.txt \
    -p HTTP \
    -r "${REGION}" \
    ${EXECUTION_ROLE_ARN:+--execution-role "${EXECUTION_ROLE_ARN}"} \
    --non-interactive \
    --deployment-type container

  # Deploy
  echo ""
  echo ">>> Deploying to AgentCore..."
  # Req 11: only bake a Bedrock key when one was explicitly supplied. Otherwise
  # omit it so the runtime mints a fresh token from its execution role at startup
  # (ad_buyer.llm.bedrock_token) — the CFN runtime role grants CallWithBearerToken.
  _key_env=()
  if [[ -n "${BEDROCK_API_KEY}" ]]; then
    _key_env=(--env "ANTHROPIC_COMPATIBLE_LLM_API_KEY=${BEDROCK_API_KEY}")
  fi
  # AWS Bedrock AgentCore CUSTOM_JWT buyer->seller path: when the seller
  # advertises an OAuth issuer, forward the seller's token endpoint + scope and
  # the buyer's OWN client credentials so crew_tools routes through the JWT
  # transport (see ad_buyer.registry.transport_selector). Each is forwarded only
  # when set — never bake an empty value. The client_secret is passed only if
  # present; prefer sourcing it from a secret store in production.
  _oauth_env=()
  [[ -n "${SELLER_MCP_RUNTIME_ARN:-}" ]] && _oauth_env+=(--env "SELLER_MCP_RUNTIME_ARN=${SELLER_MCP_RUNTIME_ARN}")
  [[ -n "${SELLER_TOKEN_ENDPOINT:-}" ]] && _oauth_env+=(--env "SELLER_TOKEN_ENDPOINT=${SELLER_TOKEN_ENDPOINT}")
  [[ -n "${SELLER_INVOKE_SCOPE:-}" ]] && _oauth_env+=(--env "SELLER_INVOKE_SCOPE=${SELLER_INVOKE_SCOPE}")
  [[ -n "${BUYER_OAUTH_CLIENT_ID:-}" ]] && _oauth_env+=(--env "BUYER_OAUTH_CLIENT_ID=${BUYER_OAUTH_CLIENT_ID}")
  [[ -n "${BUYER_OAUTH_CLIENT_SECRET:-}" ]] && _oauth_env+=(--env "BUYER_OAUTH_CLIENT_SECRET=${BUYER_OAUTH_CLIENT_SECRET}")
  agentcore deploy \
    --env "DEFAULT_LLM_MODEL=${LLM_MODEL}" \
    --env "MANAGER_LLM_MODEL=${LLM_MODEL}" \
    --env "PYTHONPATH=/app/src" \
    --env "ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL=${ANTHROPIC_BASE_URL}" \
    "${_key_env[@]}" \
    "${_oauth_env[@]}" \
    --env "STORAGE_TYPE=sqlite" \
    --env "DATABASE_URL=sqlite:///:memory:" \
    --env "ANTHROPIC_API_KEY=not-used-with-bedrock" \
    --env "SELLER_AGENT_URL=${SELLER_AGENT_URL}" \
    --env "AWS_REGION=${REGION}" \
    --env "AWS_DEFAULT_REGION=${REGION}" \
    --env "CREW_MEMORY_ENABLED=true" \
    --env "MEMORY_LLM_MODEL=${MEMORY_MODEL}" \
    --auto-update-on-conflict

  echo ""
  echo "✅ Deploy complete"
fi

# ── Test (--test or --test-only) ────────────────────────────────────
if [[ "${DO_TEST}" == "true" ]]; then
  echo ""
  echo "============================================="
  echo "  Testing: ${AGENT_NAME}"
  echo "============================================="

  # Use pytest-based integration tests if available
  TEST_RUNNER="${REPO_ROOT}/tests/integration/agentcore/run_tests.sh"
  if [[ -f "${TEST_RUNNER}" ]]; then
    echo ">>> Running pytest integration tests..."
    RUNNER_ARGS=(--profile "${AWS_PROFILE:-}")
    if [[ -n "${AGENT_NAME}" ]]; then
      RUNNER_ARGS+=(--agent-name "${AGENT_NAME}")
    fi
    bash "${TEST_RUNNER}" "${RUNNER_ARGS[@]}"
    exit $?
  fi

  # Fallback: inline tests (if pytest runner not found)
  echo "  ⚠️  pytest runner not found at ${TEST_RUNNER} — using inline tests"

  # Resolve runtime ARN and log group from yaml
  RUNTIME_ARN=""
  if command -v python3 &>/dev/null && [[ -f .bedrock_agentcore.yaml ]]; then
    RUNTIME_ARN=$(python3 -c "
import yaml
with open('.bedrock_agentcore.yaml') as f:
    cfg = yaml.safe_load(f)
agent = cfg['agents'].get('${AGENT_NAME}', {})
bc = agent.get('bedrock_agentcore', {})
print(bc.get('agent_arn', ''))
" 2>/dev/null || true)
  fi

  if [[ -n "${RUNTIME_ARN}" ]]; then
    RUNTIME_ID=$(echo "${RUNTIME_ARN}" | awk -F'/' '{print $2}')
    LOG_GROUP="/aws/bedrock-agentcore/runtimes/${RUNTIME_ID}-DEFAULT"
    DATE_PREFIX=$(date -u +"%Y/%m/%d")
    echo "  ARN:  ${RUNTIME_ARN}"
    echo "  Logs: ${LOG_GROUP}"
  fi

  # Wait briefly for container startup
  sleep 5

  TESTS_PASSED=0
  TESTS_FAILED=0

  # ── Test 1: Chat mode (simple query) ──
  CHAT_PROMPT='{"prompt": "What campaigns are active?", "routing_mode": "chat"}'
  echo ""
  echo "--- Testing Chat mode (simple): ${AGENT_NAME} ---"
  echo "ARN:  ${RUNTIME_ARN:-<unknown>}"
  INVOKE_OUTPUT=$(agentcore invoke "${CHAT_PROMPT}" 2>&1) || true
  # Extract response content — skip the agentcore CLI box header
  RESPONSE_TEXT=$(echo "${INVOKE_OUTPUT}" | sed -n '/^Response:/,$ p' | head -40)
  if [[ -z "${RESPONSE_TEXT}" ]]; then
    # Fallback: show last 40 lines if no "Response:" marker found
    RESPONSE_TEXT=$(echo "${INVOKE_OUTPUT}" | grep -v '│\|╭\|╰\|╮\|─' | tail -40)
  fi
  echo "--- Response ---"
  echo "${RESPONSE_TEXT}"
  echo "---"

  if echo "${INVOKE_OUTPUT}" | grep -qi '"error":\|"exception":\|Invocation failed\|RuntimeClientError\|32010'; then
    echo "❌ Chat mode (simple) FAILED"
    TESTS_FAILED=$((TESTS_FAILED + 1))
  else
    echo "✅ Chat mode (simple) PASSED"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  fi

  # ── Test 2: Crew mode (complex campaign brief) ──
  CREW_PROMPT='{"prompt": "Plan a $500K Q4 automotive campaign across CTV and digital video. Allocate budget, research inventory, and recommend deals.", "routing_mode": "crew"}'
  echo ""
  echo "--- Testing Crew mode (complex): ${AGENT_NAME} ---"
  echo "ARN:  ${RUNTIME_ARN:-<unknown>}"
  MAX_ATTEMPTS=3
  ATTEMPT=1
  CREW_PASSED=false

  while [[ ${ATTEMPT} -le ${MAX_ATTEMPTS} ]]; do
    echo "Invoking (attempt ${ATTEMPT}/${MAX_ATTEMPTS}): ${CREW_PROMPT}"
    INVOKE_OUTPUT=$(agentcore invoke "${CREW_PROMPT}" 2>&1) || true
    # Extract response content — skip the agentcore CLI box header
    RESPONSE_TEXT=$(echo "${INVOKE_OUTPUT}" | sed -n '/^Response:/,$ p' | head -50)
    if [[ -z "${RESPONSE_TEXT}" ]]; then
      RESPONSE_TEXT=$(echo "${INVOKE_OUTPUT}" | grep -v '│\|╭\|╰\|╮\|─' | tail -50)
    fi
    echo "--- Response ---"
    echo "${RESPONSE_TEXT}"
    echo "---"

    if echo "${INVOKE_OUTPUT}" | grep -qi '"error":\|"exception":\|Invocation failed\|RuntimeClientError\|32010'; then
      echo "  ⚠️  Attempt ${ATTEMPT} failed"
      ATTEMPT=$((ATTEMPT + 1))
      sleep 3
    else
      CREW_PASSED=true
      break
    fi
  done

  if [[ "${CREW_PASSED}" == "true" ]]; then
    echo "✅ Crew mode (complex) PASSED"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  else
    echo "❌ Crew mode (complex) FAILED (after ${MAX_ATTEMPTS} attempts)"
    TESTS_FAILED=$((TESTS_FAILED + 1))

    if [[ -n "${RUNTIME_ARN:-}" ]]; then
      echo ""
      echo "CloudWatch logs (last 5 min):"
      aws logs tail "${LOG_GROUP}" \
        --log-stream-name-prefix "${DATE_PREFIX}/[runtime-logs]" \
        --since 5m \
        --format short \
        --region "${REGION}" 2>&1 \
        | grep -v "opentelemetry.instrumentation" \
        | grep -v "otelTrace" \
        | grep -v "resource.service" \
        | tail -30
    fi
  fi

  # ── Summary ──
  echo ""
  echo "============================================="
  if [[ ${TESTS_FAILED} -gt 0 ]]; then
    echo "  ❌ ${TESTS_FAILED} TEST(S) FAILED"
    echo "============================================="
    exit 1
  else
    echo "  ✅ ALL TESTS PASSED"
    echo "============================================="
  fi
fi

# ── Status (deploy only, no --test) ────────────────────────────────
if [[ "${DO_TEST}" == "false" ]]; then
  echo ""
  echo ">>> Deployment status..."
  agentcore status --verbose

  echo ""
  echo "============================================="
  echo "  Deployment Complete"
  echo "============================================="
fi
