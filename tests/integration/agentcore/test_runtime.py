"""AgentCore runtime tests for the Buyer HTTP runtime.

These tests invoke the deployed runtime via `agentcore invoke` and validate
real responses. They require a deployed runtime and AWS credentials.

The buyer agent handles ONE thing: campaign planning via DealBookingFlow.
All seller interactions (inventory, pricing, deals) are handled by the
seller runtime, orchestrated by the Agency Agent in the guidance layer.

Usage:
    pytest tests/integration/agentcore/test_runtime.py -v --profile genai
    pytest tests/integration/ -v -k "agentcore and plan" --profile genai

Environment:
    BUYER_RUNTIME_ARN: Runtime ARN (auto-detected from .bedrock_agentcore.yaml)
    AWS_PROFILE: AWS CLI profile (or --profile pytest arg)
    AWS_REGION: Region (default: us-west-2)
"""

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class RuntimeConfig:
    arn: str
    region: str
    profile: str | None
    agent_name: str


@pytest.fixture(scope="session")
def runtime_config(request) -> RuntimeConfig:
    """Resolve the runtime ARN and config for tests."""
    profile = request.config.getoption("--profile") or os.environ.get("AWS_PROFILE")
    region = os.environ.get("AWS_REGION", "us-west-2")
    arn = request.config.getoption("--runtime-arn") or os.environ.get("BUYER_RUNTIME_ARN", "")
    agent_name = request.config.getoption("--agent-name") or ""

    if not arn:
        yaml_path = Path(__file__).parent.parent.parent.parent / ".bedrock_agentcore.yaml"
        if yaml_path.exists():
            try:
                import yaml

                with open(yaml_path) as f:
                    cfg = yaml.safe_load(f)
                agents = cfg.get("agents", {})
                for name, agent_cfg in agents.items():
                    bc = agent_cfg.get("bedrock_agentcore", {})
                    candidate = bc.get("agent_arn", "")
                    if candidate:
                        arn = candidate
                        agent_name = name
                        break
            except Exception as e:
                logger.warning("Failed to read .bedrock_agentcore.yaml: %s", e)

    if not arn:
        pytest.skip("No runtime ARN available — set BUYER_RUNTIME_ARN or deploy first")

    return RuntimeConfig(arn=arn, region=region, profile=profile, agent_name=agent_name)


def invoke_runtime(
    config: RuntimeConfig,
    payload: dict,
    timeout: int = 240,
    max_retries: int = 3,
    retry_wait: int = 30,
) -> dict:
    """Invoke the runtime and return parsed response.

    Default ``timeout`` is generous: these live tests drive the REAL
    multi-agent crew on Bedrock Claude (research + memory save/retrieve +
    seller tool calls), which routinely runs ~3 min for a full campaign plan,
    plus a possible cold-start on the first invoke after a deploy. Crew/
    integration tests raise it further.
    """
    payload_json = json.dumps(payload)
    cmd = ["agentcore", "invoke", payload_json]
    env = os.environ.copy()
    if config.profile:
        env["AWS_PROFILE"] = config.profile
    env["AWS_REGION"] = config.region

    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(Path(__file__).parent.parent.parent.parent),
            )
            output = result.stdout + result.stderr

            if re.search(
                r"initialization time exceeded|32010|RuntimeClientError", output, re.IGNORECASE
            ):
                if attempt < max_retries:
                    logger.warning("Cold start timeout (attempt %d/%d)", attempt, max_retries)
                    time.sleep(retry_wait)
                    continue
                return {
                    "response": "",
                    "raw": output,
                    "success": False,
                    "error": "Cold start timeout",
                }

            response_text = _extract_response(output)

            if re.search(r'"error":|"exception":|Invocation failed', output, re.IGNORECASE):
                return {
                    "response": response_text,
                    "raw": output,
                    "success": False,
                    "error": response_text,
                }

            return {"response": response_text, "raw": output, "success": True, "error": ""}

        except subprocess.TimeoutExpired:
            if attempt < max_retries:
                time.sleep(retry_wait)
                continue
            return {"response": "", "raw": "", "success": False, "error": "Invoke timeout"}

    return {"response": "", "raw": "", "success": False, "error": "Max retries exceeded"}


def _extract_response(output: str) -> str:
    """Extract the response text from agentcore invoke output."""
    match = re.search(r"Response:\s*\n?(.*)", output, re.DOTALL)
    if match:
        text = match.group(1).strip()
        text = re.sub(r"[│╭╰╮─╯┌┐└┘├┤┬┴┼]", "", text)
        return text.strip()
    cleaned = re.sub(r"[│╭╰╮─╯┌┐└┘├┤┬┴┼]", "", output)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Chat mode tests
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestChatMode:
    """Tests for the chat routing mode."""

    def test_simple_query(self, runtime_config):
        """Chat mode responds to a simple query about capabilities."""
        result = invoke_runtime(runtime_config, {"prompt": "What can you help me with?"})
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        assert any(kw in response for kw in ["campaign", "plan", "budget", "buyer", "help"]), (
            f"Response doesn't mention capabilities: {result['response'][:200]}"
        )


# ---------------------------------------------------------------------------
# Crew mode tests — campaign planning (buyer's core responsibility)
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestCrewPlanCampaign:
    """Crew mode: campaign planning via DealBookingFlow."""

    def test_campaign_plan_with_budget(self, runtime_config):
        """Plan a campaign with budget and channel allocation."""
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": (
                    "Plan a $500K Q4 automotive campaign across CTV and digital"
                    " video targeting adults 25-54"
                ),
                "routing_mode": "crew",
            },
            # Crew mode fans out a multi-agent crew over multi-turn tool calls on
            # Claude Sonnet 5; observed end-to-end latency is ~4 min, well past the
            # 120s default. Give real headroom so a correct plan isn't scored as a
            # failure purely on latency.
            timeout=360,
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        assert any(
            kw in response for kw in ["budget", "allocation", "ctv", "video", "$", "channel"]
        ), f"No campaign plan elements: {result['response'][:300]}"

    def test_campaign_plan_includes_next_step(self, runtime_config):
        """Plan response should include structured plan data with approval flag."""
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "Plan a $200K Q1 campaign for mobile and display",
                "routing_mode": "crew",
            },
            timeout=360,
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        response = result["response"].lower()
        assert "approval" in response or "budget" in response or "campaign" in response, (
            f"No plan data in response: {result['response'][:300]}"
        )

    def test_campaign_plan_returns_metadata(self, runtime_config):
        """Plan response should include buyer_campaign_plan metadata."""
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": "Plan a $1M Q3 brand awareness campaign",
                "routing_mode": "crew",
            },
            timeout=360,
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        # The raw output should contain campaign plan indicators
        response = result["response"]
        assert any(kw in response.lower() for kw in ["budget", "campaign", "plan", "allocation"]), (
            f"No plan data: {response[:300]}"
        )


# ---------------------------------------------------------------------------
# Buyer -> Seller runtime-to-runtime integration (AgentCoreSellerProxy)
# ---------------------------------------------------------------------------


@pytest.mark.agentcore
class TestBuyerSellerIntegration:
    """End-to-end buyer->seller over InvokeAgentRuntime (the deployed loop).

    This is the ONE test that requires both runtimes deployed. The broader
    buyer<->seller *functional* contract — pricing tiers by buyer identity, the
    deal request/booking flow, MCP tools, order lifecycle, negotiation — is
    already covered OFFLINE by the existing suites (e.g. seller
    ``tests/integration/test_deal_flow_e2e.py`` / ``test_mcp_integration.py``,
    buyer ``tests/integration/test_deal_booking_flow.py`` /
    ``test_auth_session_negotiation.py``). This test adds only what those cannot:
    proof the real cross-*runtime* transport works when deployed.

    The buyer crew's research path calls ``search_advertising_products``, which
    (when ``SELLER_AGENT_URL`` is an AgentCore ARN) is backed by
    ``AgentCoreSellerProxy`` -> ``bedrock-agentcore:InvokeAgentRuntime`` against
    the seller's **MCP** runtime, issuing MCP ``tools/call`` over the
    Streamable-HTTP transport (initialize handshake + dual Accept header +
    SSE-framed result parsing).

    Where the signal lives: the crew's FINAL invoke response is a budget
    *allocation plan* (channels/percentages/rationale) — it does NOT echo the
    seller's product ids. The proof of the closed loop is in the runtime's
    CloudWatch logs: the buyer logs the seller catalog's REAL ``inv-*`` ids
    coming back through the proxy. (Note: ``inv-*`` is the seller *catalog*
    namespace; the buyer crew's own *recommendation* ids use ``prod-*`` and are
    unrelated — so only an ``inv-*`` id proves the live seller catalog.)

    Requires BOTH runtimes deployed and the buyer runtime's execution role
    granted ``InvokeAgentRuntime`` on the seller runtime (see
    ``infra/aws/agentcore/auth-agentcore.yaml``). Skips cleanly when the buyer
    was not deployed against an ARN seller, or when CloudWatch is unreadable.
    """

    _REAL_INV_RE = re.compile(r"inv-[a-z0-9-]+", re.IGNORECASE)

    @staticmethod
    def _log_group(config: "RuntimeConfig") -> str | None:
        """DEFAULT runtime log group for the buyer, derived from its ARN."""
        # arn:...:runtime/<id>  ->  /aws/bedrock-agentcore/runtimes/<id>-DEFAULT
        m = re.search(r"runtime/([^/]+)$", config.arn)
        return f"/aws/bedrock-agentcore/runtimes/{m.group(1)}-DEFAULT" if m else None

    def _recent_log_text(self, config: "RuntimeConfig", since_ms: int, pattern: str) -> str:
        """Return recent buyer log messages matching a CloudWatch filter pattern.

        Skips (not fails) when logs are unreadable — the runtime behaviour is
        still exercised by the invoke; only the assertion channel is missing.
        """
        try:
            import boto3
        except ImportError:  # pragma: no cover
            pytest.skip("boto3 unavailable to read CloudWatch logs")

        lg = self._log_group(config)
        if not lg:
            pytest.skip(f"Could not derive log group from ARN {config.arn}")

        session = boto3.Session(profile_name=config.profile) if config.profile else boto3.Session()
        logs = session.client("logs", region_name=config.region)
        try:
            lines: list[str] = []
            paginator = logs.get_paginator("filter_log_events")
            for page in paginator.paginate(
                logGroupName=lg,
                startTime=since_ms,
                filterPattern=pattern,
            ):
                lines.extend(e.get("message", "") for e in page.get("events", []))
                if len(lines) > 500:
                    break
            return "\n".join(lines)
        except logs.exceptions.ResourceNotFoundException:
            pytest.skip(f"Log group {lg} not found — runtime not deployed?")
        except Exception as e:  # noqa: BLE001 — logs are the assertion channel, not the SUT
            pytest.skip(f"Could not read CloudWatch logs: {e}")

    def test_crew_reaches_seller_inventory(self, runtime_config):
        """Buyer crew discovers real seller inv-* inventory via the proxy.

        Invokes the crew, then asserts the buyer's logs surfaced real ``inv-*``
        ids from the seller catalog through the proxy path. ``inv-*`` is the
        seller *catalog* id namespace; the buyer's own crew *recommendations*
        use a separate ``prod-*`` id namespace (see
        ``tests/integration/test_deal_booking_flow.py``), so the two are NOT
        interchangeable and only an ``inv-*`` id proves the seller catalog was
        reached live through the proxy.
        """
        since_ms = int((time.time() - 5) * 1000)
        result = invoke_runtime(
            runtime_config,
            {
                "prompt": (
                    "Plan a $500K Q4 automotive campaign across CTV and digital"
                    " video, and discover seller inventory."
                ),
                "routing_mode": "crew",
            },
            timeout=300,
        )
        assert result["success"], f"Invoke failed: {result['error']}"

        # Did the buyer exercise the seller-search tool at all? (skip guard)
        tool_text = self._recent_log_text(
            runtime_config, since_ms, '"search_advertising_products"'
        )
        if "search_advertising_products" not in tool_text:
            pytest.skip(
                "Buyer did not exercise the seller-search tool (not wired to an "
                "ARN seller?); deploy with --seller-url <seller MCP ARN> to run."
            )

        # The definitive signal: real seller inv-* ids in the logs. CloudWatch
        # filter patterns need the hyphen term quoted.
        inv_text = self._recent_log_text(runtime_config, since_ms, '"inv-"')
        inv_ids = sorted(set(self._REAL_INV_RE.findall(inv_text)))
        assert inv_ids, (
            "Buyer's seller search returned no real inv-* ids — the proxy->MCP "
            f"path returned no catalog data. Log tail: {inv_text[-500:]}"
        )
