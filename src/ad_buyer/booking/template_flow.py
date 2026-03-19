# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Template-based booking module (stub).

Will call POST /api/v1/deals/from-template when the seller-side
template API is implemented. For now, this is a placeholder that
defines the interface for template-based deal creation.

See: buyer-te6b.2.8 (InstantiateDealFromTemplateTool)
"""

from typing import Any, Optional

from ..models.buyer_identity import BuyerContext


class TemplateFlowClient:
    """Client for template-based deal creation.

    This is a stub implementation. The full implementation will call
    POST /api/v1/deals/from-template on the seller side.

    Example (future):
        client = TemplateFlowClient(
            buyer_context=buyer_context,
            seller_base_url="http://localhost:5000",
        )
        deal = await client.create_from_template(
            template_id="tmpl-001",
            overrides={"impressions": 1_000_000},
        )
    """

    def __init__(
        self,
        buyer_context: BuyerContext,
        seller_base_url: str,
    ) -> None:
        """Initialize the template flow client.

        Args:
            buyer_context: Buyer context with identity for tiered access.
            seller_base_url: Base URL of the seller's API.
        """
        self._buyer_context = buyer_context
        self._seller_base_url = seller_base_url

    async def create_from_template(
        self,
        template_id: str,
        overrides: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create a deal from a seller template.

        Stub implementation -- will call POST /api/v1/deals/from-template
        when the seller-side API is ready.

        Args:
            template_id: Template ID to instantiate.
            overrides: Optional dict of field overrides.

        Returns:
            Deal data dict.

        Raises:
            NotImplementedError: Always, until seller template API is ready.
        """
        raise NotImplementedError(
            "Template-based booking requires seller-side template API "
            "(POST /api/v1/deals/from-template). "
            "See buyer-te6b.2.8 for implementation tracking."
        )
