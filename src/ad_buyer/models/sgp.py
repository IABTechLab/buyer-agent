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

# Accepted values for the unknown-vendor policy, shared by every caller so the
# gate cannot disagree with itself about what a policy string means.
#
# This lives with the models rather than the client so that ``config.settings``
# can validate the policy without importing ``ad_buyer.clients``, which pulls in
# every client module -- one of which reads settings itself.
UNKNOWN_VENDOR_POLICIES = frozenset({"block", "warn", "allow"})


def normalize_unknown_policy(value: str) -> str:
    """Canonicalize an unknown-vendor policy, raising on unrecognized input.

    Case- and whitespace-insensitive, so ``"BLOCK"`` from an env var resolves
    to ``"block"`` rather than silently falling through to whichever branch
    happens to be the fallback.

    Raises:
        ValueError: if the value is not one of ``UNKNOWN_VENDOR_POLICIES``.
    """
    candidate = (value or "").strip().lower()
    if candidate not in UNKNOWN_VENDOR_POLICIES:
        raise ValueError(
            f"Invalid sgp_unknown_policy {value!r}. "
            f"Must be one of: {', '.join(sorted(UNKNOWN_VENDOR_POLICIES))}"
        )
    return candidate


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
