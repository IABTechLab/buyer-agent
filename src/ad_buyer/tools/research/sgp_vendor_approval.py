# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""CrewAI tool: check IAB buyer-agent approval via SafeGuard Privacy.

The class is intentionally prefixed ``SGP`` so that future vendor-approval
integrations (e.g. OneTrust, an IAB Tech Lab registry) can coexist under
distinct class names and distinct CrewAI tool ``name`` attributes.
"""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...clients.sgp_client import SGPClient, SGPClientError


class SGPVendorApprovalInput(BaseModel):
    """Input schema for the SafeGuard Privacy vendor approval check tool."""

    domains: list[str] = Field(
        ...,
        description=(
            "Seller domains or full seller URLs to check. Scheme, www, and "
            "port are stripped automatically. Up to 10 are checked per call; "
            "larger lists are batched."
        ),
    )


class SGPVendorApprovalTool(BaseTool):
    """Check whether seller vendors carry the IAB buyer-agent approval flag.

    Consults the SafeGuard Privacy `iab/buyer-agent-approval` endpoint,
    which returns the ``iabBuyerAgentApproval`` boolean per vendor on the
    buyer's SGP tenant. Vendors absent from the tenant come back as
    ``UNKNOWN``.
    """

    name: str = "check_sgp_vendor_approval"
    description: str = (
        "Check IAB buyer-agent approval for seller domains via SafeGuard "
        "Privacy. Returns APPROVED / NOT APPROVED / UNKNOWN per domain, "
        "along with the approval date when available. Use before "
        "requesting a Deal ID from a seller."
    )
    args_schema: type[BaseModel] = SGPVendorApprovalInput
    _client: SGPClient

    def __init__(self, client: SGPClient, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = client

    def _run(self, domains: list[str]) -> str:
        return run_async(self._arun(domains=domains))

    async def _arun(self, domains: list[str]) -> str:
        try:
            results = await self._client.check_approvals(domains)
        except SGPClientError as exc:
            return f"SafeGuard Privacy lookup failed: {exc}"

        if not results:
            return "No valid domains were provided."

        lines = [
            "SafeGuard Privacy — IAB Buyer-Agent Approval",
            "-" * 50,
        ]
        for domain in sorted(results):
            record = results[domain]
            if record is None:
                lines.append(f"? {domain}: UNKNOWN (not in SGP portfolio)")
                continue
            if record.iab_buyer_agent_approval:
                approved_at = (
                    record.iab_buyer_agent_approved_at.isoformat()
                    if record.iab_buyer_agent_approved_at
                    else "date unknown"
                )
                lines.append(
                    f"✓ {domain}: APPROVED "
                    f"({record.company_name or 'company name unavailable'}, "
                    f"since {approved_at})"
                )
            else:
                lines.append(
                    f"✗ {domain}: NOT APPROVED "
                    f"({record.company_name or 'company name unavailable'})"
                )

        return "\n".join(lines)
