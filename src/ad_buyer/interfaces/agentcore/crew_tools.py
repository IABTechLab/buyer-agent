"""Campaign planning for the Buyer AgentCore HTTP runtime.

Calls DealBookingFlow directly (in-process, same as CLI mode).
The inner PortfolioCrew uses its own LLM call to extract campaign
parameters from the natural language prompt and allocate budget.

Architecture:
    http_main.py → run_campaign_plan(prompt) → DealBookingFlow (in-process)
                                                     ↓
                                                PortfolioCrew (Bedrock LLM)
                                                     ↓
                                                Channel specialists (parallel)
                                                     ↓
                                                Recommendations (awaiting approval)

This file lives in the agentcore interface directory so it doesn't modify
the community-maintained agent/crew code.
"""

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def _parse_brief_from_prompt(prompt: str) -> dict:
    """Extract structured brief fields from a natural language prompt.

    The DealBookingFlow's PortfolioCrew needs a structured brief as input —
    it allocates budget *within* the brief, it doesn't re-extract from text.
    This function pulls the concrete numbers (budget, dates, channels) so
    the PortfolioCrew gets the right inputs to reason over.

    This is input parsing, not decision-making — same as parsing a JSON
    payload. The LLM inside PortfolioCrew does the actual planning.
    """

    # Budget
    budget = 100000
    m = re.search(r"\$\s*([\d,]+)\s*K\b", prompt, re.IGNORECASE)
    if m:
        budget = float(m.group(1).replace(",", "")) * 1000
    else:
        m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*M\b", prompt, re.IGNORECASE)
        if m:
            budget = float(m.group(1).replace(",", "")) * 1_000_000

    # Quarter → dates
    start_date, end_date = "2026-10-01", "2026-12-31"
    quarter_map = {
        "q1": ("2026-01-01", "2026-03-31"),
        "q2": ("2026-04-01", "2026-06-30"),
        "q3": ("2026-07-01", "2026-09-30"),
        "q4": ("2026-10-01", "2026-12-31"),
    }
    qm = re.search(r"\b(Q[1-4])\b", prompt, re.IGNORECASE)
    if qm:
        start_date, end_date = quarter_map.get(qm.group(1).lower(), (start_date, end_date))

    # Audience
    audience = "general"
    am = re.search(r"targeting\s+(.+?)(?:\.|,|$)", prompt, re.IGNORECASE)
    if am:
        audience = am.group(1).strip()

    return {
        "name": prompt[:120],
        "objectives": ["awareness", "consideration"],
        "budget": budget,
        "start_date": start_date,
        "end_date": end_date,
        "target_audience": audience,
    }


def run_campaign_plan(prompt: str, brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the DealBookingFlow planning stages with the given prompt.

    The brief can be pre-extracted by a lightweight LLM call in http_main.py,
    or this function will use the prompt directly as the campaign name
    and let the inner PortfolioCrew handle extraction.

    Args:
        prompt: Natural language campaign planning request.
        brief: Pre-extracted campaign brief dict (from Haiku/Nova Lite).
               If None, a minimal brief is constructed from the prompt.

    Returns:
        Dict with campaign plan, budget allocations, recommendations,
        and approval_required=True.
    """
    from ad_buyer.clients.opendirect_client import OpenDirectClient
    from ad_buyer.flows.deal_booking_flow import DealBookingFlow
    from ad_buyer.models.flow_state import BookingState

    # Override buyer settings to use Bedrock instead of Anthropic.
    bedrock_model = os.environ.get(
        "DEFAULT_LLM_MODEL",
        "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    )
    from ad_buyer.config.settings import settings as buyer_settings

    buyer_settings.manager_llm_model = bedrock_model
    buyer_settings.default_llm_model = bedrock_model

    # Use pre-extracted brief or parse one from the prompt
    if brief is None:
        brief = _parse_brief_from_prompt(prompt)

    # Ensure target_audience is a dict (LLM may return a string)
    if isinstance(brief.get("target_audience"), str):
        brief["target_audience"] = {"description": brief["target_audience"]}

    # Ensure objectives is a list
    if isinstance(brief.get("objectives"), str):
        brief["objectives"] = [brief["objectives"]]

    # Ensure budget is positive
    if not brief.get("budget") or float(brief.get("budget", 0)) <= 0:
        brief["budget"] = 100000

    logger.info("Campaign brief: %s", json.dumps(brief, default=str))

    # Create client — dummy URL since seller is a separate AgentCore runtime
    seller_url = os.environ.get("SELLER_AGENT_URL", "http://localhost:8001")
    client_url = "http://localhost:9999" if seller_url.startswith("arn:") else seller_url
    client = OpenDirectClient(base_url=client_url)

    flow = DealBookingFlow(client)
    try:
        flow.state = BookingState(campaign_brief=brief)
    except AttributeError:
        flow._state = BookingState(campaign_brief=brief)

    # Run the flow — PortfolioCrew does the LLM-based budget allocation
    flow.kickoff()

    # Build response from flow state — planning only, no booking
    response: dict[str, Any] = {
        "campaign_name": brief["name"],
        "total_budget": brief["budget"],
        "flight": f"{brief['start_date']} to {brief['end_date']}",
        "status": flow.state.execution_status.value,
        "approval_required": True,
    }

    if flow.state.budget_allocations:
        response["budget_allocations"] = {
            ch: {
                "budget": alloc.budget,
                "percentage": alloc.percentage,
                "rationale": alloc.rationale,
            }
            for ch, alloc in flow.state.budget_allocations.items()
        }

    if flow.state.pending_approvals:
        response["recommendations"] = [
            {
                "product_id": rec.product_id,
                "product_name": rec.product_name,
                "channel": rec.channel,
                "publisher": rec.publisher,
                "impressions": rec.impressions,
                "cpm": rec.cpm,
                "cost": rec.cost,
            }
            for rec in flow.state.pending_approvals
        ]

    if flow.state.audience_coverage_estimates:
        response["audience_coverage"] = flow.state.audience_coverage_estimates

    if flow.state.audience_gaps:
        response["audience_gaps"] = flow.state.audience_gaps

    if flow.state.errors:
        response["errors"] = flow.state.errors

    return response
