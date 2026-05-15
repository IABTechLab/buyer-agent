# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""IAB Diligence Platform (SGP) integration models.

Mirrors the IabBuyerAgentResource returned by
    GET /api/v1/integrations/iab/buyer-agent-approval
on the IAB Diligence Platform platform.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApprovalRecord(BaseModel):
    """A single vendor's IAB buyer-agent approval status from IAB Diligence Platform."""

    model_config = ConfigDict(populate_by_name=True)

    vendor_id: int = Field(alias="vendorId")
    vendor_company_id: int = Field(alias="vendorCompanyId")
    company_name: str = Field(alias="companyName", default="")
    domain: str = ""
    iab_buyer_agent_approval: bool = Field(alias="iabBuyerAgentApproval", default=False)
    iab_buyer_agent_approved_at: datetime | None = Field(
        alias="iabBuyerAgentApprovedAt", default=None
    )
