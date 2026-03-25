# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Template-based booking module.

Provides the TemplateFlowClient that calls POST /api/v1/deals/from-template
on the seller side. The actual deal creation logic (template resolution,
override application, portfolio storage, and event emission) lives in
ad_buyer.tools.deal_library.instantiate_from_template.

See: buyer-te6b.2.8 (InstantiateDealFromTemplateTool)
"""

from typing import Any

from ..models.buyer_identity import BuyerContext


class TemplateFlowClient:
    """Client for template-based deal creation.

    Calls the seller POST /api/v1/deals/from-template endpoint to
    create deals from stored templates. Used by
    InstantiateDealFromTemplateTool internally.

    Example:
        client = TemplateFlowClient(
            buyer_context=buyer_context,
            seller_base_url="http://localhost:5000",
        )
        result = client.create_from_template(
            template_id="tmpl-001",
            buyer_params={"max_cpm": 25.0, "impressions": 1_000_000},
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

    def create_from_template(
        self,
        template_id: str,
        buyer_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a deal from a seller template.

        Calls POST /api/v1/deals/from-template on the seller side.
        Currently uses a stub response until the seller-side API is
        fully implemented. The real HTTP call will be added when the
        seller endpoint is available.

        Args:
            template_id: Template ID to instantiate.
            buyer_params: Buyer parameters (max_cpm, impressions, etc.).

        Returns:
            Dict with success/rejection and deal data.
        """
        if buyer_params is None:
            buyer_params = {}

        # Stub: simulate seller response
        # TODO: Replace with real HTTP POST to seller API when available
        return {
            "success": True,
            "deal": {
                "seller_deal_id": f"seller-{template_id}-deal",
                "deal_type": buyer_params.get("deal_type", "PG"),
                "price": buyer_params.get("max_cpm", 0),
                "impressions": buyer_params.get("impressions", 0),
                "flight_start": buyer_params.get("flight_start"),
                "flight_end": buyer_params.get("flight_end"),
                "product_id": f"prod-from-{template_id}",
                "product_name": f"Product from template {template_id}",
                "seller_url": self._seller_base_url,
            },
        }
