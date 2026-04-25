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
from typing import Optional

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@dataclass
class RuntimeConfig:
    arn: str
    region: str
    profile: Optional[str]
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
    timeout: int = 120,
    max_retries: int = 3,
    retry_wait: int = 30,
) -> dict:
    """Invoke the runtime and return parsed response."""
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

            if re.search(r"initialization time exceeded|32010|RuntimeClientError", output, re.IGNORECASE):
                if attempt < max_retries:
                    logger.warning("Cold start timeout (attempt %d/%d)", attempt, max_retries)
                    time.sleep(retry_wait)
                    continue
                return {"response": "", "raw": output, "success": False, "error": "Cold start timeout"}

            response_text = _extract_response(output)

            if re.search(r'"error":|"exception":|Invocation failed', output, re.IGNORECASE):
                return {"response": response_text, "raw": output, "success": False, "error": response_text}

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
        assert any(
            kw in response for kw in ["campaign", "plan", "budget", "buyer", "help"]
        ), f"Response doesn't mention capabilities: {result['response'][:200]}"


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
                "prompt": "Plan a $500K Q4 automotive campaign across CTV and digital video targeting adults 25-54",
                "routing_mode": "crew",
            },
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
        )
        assert result["success"], f"Invoke failed: {result['error']}"
        # The raw output should contain campaign plan indicators
        response = result["response"]
        assert any(
            kw in response.lower() for kw in ["budget", "campaign", "plan", "allocation"]
        ), f"No plan data: {response[:300]}"
