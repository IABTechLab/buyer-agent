# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Inventory discovery tool for buyer deal workflows."""

import logging
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...booking.pricing import PricingCalculator
from ...clients.sgp_client import SGPClient, SGPClientError, extract_product_domain
from ...clients.unified_client import UnifiedClient
from ...models.buyer_identity import BuyerContext
from ...models.sgp import ApprovalRecord

logger = logging.getLogger(__name__)


class DiscoverInventoryInput(BaseModel):
    """Input schema for inventory discovery tool."""

    query: str | None = Field(
        default=None,
        description="Natural language query for inventory (e.g., 'CTV inventory under $25 CPM')",
    )
    channel: str | None = Field(
        default=None,
        description="Channel filter (e.g., 'ctv', 'display', 'video', 'mobile')",
    )
    max_cpm: float | None = Field(
        default=None,
        description="Maximum CPM price filter",
        ge=0,
    )
    min_impressions: int | None = Field(
        default=None,
        description="Minimum available impressions filter",
        ge=0,
    )
    targeting: list[str] | None = Field(
        default=None,
        description="Required targeting capabilities (e.g., ['household', 'geo', 'demographic'])",
    )
    publisher: str | None = Field(
        default=None,
        description="Specific publisher to search",
    )


class DiscoverInventoryTool(BaseTool):
    """Discover available advertising inventory from sellers with identity-based access.

    This tool queries sellers for available inventory, presenting the buyer's
    identity context to unlock tiered pricing and premium inventory access.

    Access tiers:
    - Public: Price ranges only, limited catalog
    - Seat: Fixed prices with 5% discount
    - Agency: 10% discount, premium inventory access
    - Advertiser: 15% discount, full negotiation capability
    """

    name: str = "discover_inventory"
    description: str = """Discover available advertising inventory from sellers.
Presents buyer identity to unlock tiered pricing and premium access.

Args:
    query: Natural language query (e.g., 'CTV inventory under $25 CPM')
    channel: Channel filter ('ctv', 'display', 'video', 'mobile')
    max_cpm: Maximum CPM price
    min_impressions: Minimum available impressions
    targeting: Required targeting capabilities
    publisher: Specific publisher to search

Returns:
    List of available products with pricing based on buyer's access tier."""

    args_schema: type[BaseModel] = DiscoverInventoryInput
    _client: UnifiedClient
    _buyer_context: BuyerContext
    _sgp_client: SGPClient | None
    _sgp_enforce: bool
    _sgp_unknown_policy: str

    def __init__(
        self,
        client: UnifiedClient,
        buyer_context: BuyerContext,
        sgp_client: SGPClient | None = None,
        sgp_enforce: bool = False,
        sgp_unknown_policy: str = "block",
        **kwargs: Any,
    ):
        """Initialize with unified client and buyer context.

        Args:
            client: UnifiedClient for seller communication
            buyer_context: BuyerContext with identity for tiered access
            sgp_client: Optional SafeGuard Privacy client. When provided,
                each returned product is annotated with the seller's
                IAB buyer-agent approval status.
            sgp_enforce: When True and ``sgp_client`` is provided, NOT
                APPROVED vendors are removed from the result before the
                agent sees them, and an SGP transport failure halts the
                flow instead of falling back to unannotated results.
            sgp_unknown_policy: How to treat vendors absent from the
                buyer's SGP portfolio when enforcing. One of ``block``
                (filter out), ``warn`` (keep with warning annotation),
                or ``allow`` (keep silently).
        """
        super().__init__(**kwargs)
        self._client = client
        self._buyer_context = buyer_context
        self._sgp_client = sgp_client
        self._sgp_enforce = sgp_enforce
        self._sgp_unknown_policy = sgp_unknown_policy

    def _run(
        self,
        query: str | None = None,
        channel: str | None = None,
        max_cpm: float | None = None,
        min_impressions: int | None = None,
        targeting: list[str] | None = None,
        publisher: str | None = None,
    ) -> str:
        """Synchronous wrapper for async discovery."""
        return run_async(
            self._arun(
                query=query,
                channel=channel,
                max_cpm=max_cpm,
                min_impressions=min_impressions,
                targeting=targeting,
                publisher=publisher,
            )
        )

    async def _arun(
        self,
        query: str | None = None,
        channel: str | None = None,
        max_cpm: float | None = None,
        min_impressions: int | None = None,
        targeting: list[str] | None = None,
        publisher: str | None = None,
    ) -> str:
        """Discover inventory with buyer identity context."""
        try:
            # Build filters
            filters = {}
            if channel:
                filters["channel"] = channel
            if max_cpm is not None:
                filters["maxPrice"] = max_cpm
            if min_impressions is not None:
                filters["minImpressions"] = min_impressions
            if targeting:
                filters["targeting"] = targeting
            if publisher:
                filters["publisher"] = publisher

            # Add identity context to filters
            identity_context = self._buyer_context.identity.to_context_dict()
            filters["buyer_context"] = identity_context

            # Execute search
            if query:
                result = await self._client.search_products(
                    query=query,
                    filters=filters if filters else None,
                )
            else:
                result = await self._client.list_products()

            if not result.success:
                return f"Error discovering inventory: {result.error}"

            approvals = await self._fetch_approvals(result.data)
            filtered, filter_summary = self._apply_enforcement(result.data, approvals)
            return self._format_results(
                filtered, identity_context, approvals, filter_summary
            )

        except SGPClientError as e:
            # Reached only when enforcement is on; _fetch_approvals swallows
            # transport errors otherwise. Halts the flow via the caller's
            # broad except clause.
            raise SGPClientError(
                f"Inventory discovery halted: SafeGuard Privacy unreachable "
                f"while SGP_ENFORCE=true ({e})."
            ) from e
        except (OSError, ValueError, RuntimeError) as e:
            return f"Error discovering inventory: {e}"

    def _approval_line(
        self,
        product: dict,
        approvals: dict[str, ApprovalRecord | None] | None,
    ) -> str | None:
        """Render the SGP approval annotation for a single product row."""
        if not approvals or self._sgp_client is None:
            return None
        raw_domain = extract_product_domain(product)
        if not raw_domain:
            return "   SGP Approval: ? UNKNOWN (no seller domain on product)"
        normalized = self._sgp_client.normalize_domain(raw_domain)
        record = approvals.get(normalized)
        if record is None:
            # When enforcing under "allow" policy, unknowns pass silently.
            if self._sgp_enforce and self._sgp_unknown_policy == "allow":
                return None
            if self._sgp_enforce and self._sgp_unknown_policy == "warn":
                return (
                    f"   SGP WARNING: {normalized} not in SGP portfolio — "
                    f"onboard and approve to suppress this warning."
                )
            return f"   SGP Approval: ? UNKNOWN — {normalized} not in SGP portfolio"
        if record.iab_buyer_agent_approval:
            return f"   SGP Approval: ✓ APPROVED — {normalized}"
        return f"   SGP Approval: ✗ NOT APPROVED — {normalized}"

    async def _fetch_approvals(
        self, products: Any
    ) -> dict[str, ApprovalRecord | None]:
        """Batch-check SGP approvals for the distinct seller domains in the result.

        Returns a dict keyed by normalized domain. Empty dict when no
        SGP client is configured or no products carry a seller domain.
        When enforcement is off and a transport error occurs, logs and
        returns an empty dict so discovery still produces (unannotated)
        results. When enforcement is on, the transport error propagates
        so the caller can halt the flow.
        """
        if self._sgp_client is None:
            return {}
        product_list = products if isinstance(products, list) else [products]
        raw_domains: list[str] = []
        for product in product_list:
            if not isinstance(product, dict):
                continue
            domain = extract_product_domain(product)
            if domain:
                raw_domains.append(domain)
        if not raw_domains:
            return {}
        try:
            return await self._sgp_client.check_approvals(raw_domains)
        except SGPClientError:
            if self._sgp_enforce:
                raise
            logger.warning(
                "SGP approval lookup failed during discovery; "
                "continuing without annotations",
                exc_info=True,
            )
            return {}

    def _apply_enforcement(
        self,
        products: Any,
        approvals: dict[str, ApprovalRecord | None],
    ) -> tuple[list, dict[str, int]]:
        """Filter products by SGP approval status when enforcement is on.

        Returns ``(filtered_products, summary_counts)``. When enforcement
        is off, the product list is returned unchanged and counts are
        empty. ``summary_counts`` keys: ``not_approved``,
        ``unknown_blocked``, ``no_domain_blocked``.
        """
        product_list = products if isinstance(products, list) else [products]
        if not self._sgp_enforce or self._sgp_client is None:
            return product_list, {}

        filtered: list = []
        counts = {"not_approved": 0, "unknown_blocked": 0, "no_domain_blocked": 0}

        for product in product_list:
            if not isinstance(product, dict):
                filtered.append(product)
                continue
            raw_domain = extract_product_domain(product)
            if not raw_domain:
                counts["no_domain_blocked"] += 1
                continue
            normalized = self._sgp_client.normalize_domain(raw_domain)
            record = approvals.get(normalized)
            if record is None:
                if self._sgp_unknown_policy == "block":
                    counts["unknown_blocked"] += 1
                    continue
                filtered.append(product)
            elif not record.iab_buyer_agent_approval:
                counts["not_approved"] += 1
            else:
                filtered.append(product)

        return filtered, counts

    def _format_results(
        self,
        products: Any,
        identity_context: dict,
        approvals: dict[str, ApprovalRecord | None] | None = None,
        filter_summary: dict[str, int] | None = None,
    ) -> str:
        """Format discovery results with tier information."""
        if not products:
            base = "No inventory found matching your criteria."
            tail = self._filter_summary_line(filter_summary)
            return f"{base}\n{tail}" if tail else base

        tier = identity_context.get("access_tier", "public")
        discount = self._buyer_context.identity.get_discount_percentage()

        output_lines = [
            "Inventory Discovery Results",
            f"Access Tier: {tier.upper()} ({discount}% discount)",
            "-" * 50,
            "",
        ]

        # Handle both list and dict formats
        product_list = products if isinstance(products, list) else [products]

        for i, product in enumerate(product_list, 1):
            if isinstance(product, dict):
                product_id = product.get("id", "Unknown")
                name = product.get("name", "Unknown Product")
                publisher = product.get("publisherId", product.get("publisher", "Unknown"))
                base_price = product.get("basePrice", product.get("price", 0))
                channel = product.get("channel", product.get("deliveryType", "N/A"))
                impressions = product.get(
                    "availableImpressions", product.get("available_impressions", "N/A")
                )
                targeting = product.get("targeting", product.get("availableTargeting", []))

                # Calculate tiered price using centralized PricingCalculator
                if isinstance(base_price, (int, float)) and discount > 0:
                    tier_obj = self._buyer_context.identity.get_access_tier()
                    calculator = PricingCalculator()
                    pricing_result = calculator.calculate(
                        base_price=base_price,
                        tier=tier_obj,
                        tier_discount=discount,
                    )
                    price_display = f"${pricing_result.tiered_price:.2f} (was ${base_price:.2f})"
                else:
                    price_display = (
                        f"${base_price:.2f}"
                        if isinstance(base_price, (int, float))
                        else str(base_price)
                    )

                approval_line = self._approval_line(product, approvals)

                output_lines.extend(
                    [
                        f"{i}. {name}",
                        f"   Product ID: {product_id}",
                        f"   Publisher: {publisher}",
                        f"   Channel: {channel}",
                        f"   CPM: {price_display}",
                        f"   Available: {impressions:,}"
                        if isinstance(impressions, int)
                        else f"   Available: {impressions}",
                        f"   Targeting: {', '.join(targeting) if targeting else 'Standard'}",
                    ]
                )
                if approval_line:
                    output_lines.append(approval_line)
                output_lines.append("")
            else:
                output_lines.append(f"{i}. {product}")
                output_lines.append("")

        output_lines.append("-" * 50)
        output_lines.append(f"Total products found: {len(product_list)}")
        summary_line = self._filter_summary_line(filter_summary)
        if summary_line:
            output_lines.append(summary_line)

        if self._buyer_context.can_access_premium_inventory():
            output_lines.append("Premium inventory access: ENABLED")
        if self._buyer_context.can_negotiate():
            output_lines.append("Price negotiation: AVAILABLE")

        return "\n".join(output_lines)

    @staticmethod
    def _filter_summary_line(summary: dict[str, int] | None) -> str | None:
        """One-line description of products removed by SGP enforcement."""
        if not summary:
            return None
        total = sum(summary.values())
        if total == 0:
            return None
        parts: list[str] = []
        if summary.get("not_approved"):
            parts.append(f"{summary['not_approved']} not approved")
        if summary.get("unknown_blocked"):
            parts.append(f"{summary['unknown_blocked']} unknown to SGP")
        if summary.get("no_domain_blocked"):
            parts.append(f"{summary['no_domain_blocked']} missing seller domain")
        return f"SGP enforcement filtered {total} product(s): " + ", ".join(parts)
