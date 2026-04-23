# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""SafeGuard Privacy (SGP) integration models.

Mirrors the IabBuyerAgentResource returned by
    GET /api/v1/integrations/iab/buyer-agent-approval
on the SafeGuard Privacy platform.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApprovalRecord(BaseModel):
    """A single vendor's IAB buyer-agent approval status from SafeGuard Privacy."""

    model_config = ConfigDict(populate_by_name=True)

    vendor_id: int = Field(alias="vendorId")
    vendor_company_id: int = Field(alias="vendorCompanyId")
    company_name: str = Field(alias="companyName", default="")
    domain: str = ""
    internal_id: str = Field(alias="internalId", default="")
    iab_buyer_agent_approval: bool = Field(alias="iabBuyerAgentApproval", default=False)
    iab_buyer_agent_approved_at: datetime | None = Field(
        alias="iabBuyerAgentApprovedAt", default=None
    )
