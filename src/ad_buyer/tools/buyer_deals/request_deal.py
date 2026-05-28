# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal ID request tool for buyer deal workflows."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...async_utils import run_async
from ...booking.deal_id import generate_deal_id
from ...booking.pricing import PricingCalculator
from ...clients.sgp_client import SGPClient, SGPClientError, extract_product_domain
from ...clients.unified_client import UnifiedClient
from ...models.audience_plan import AudiencePlan
from ...models.buyer_identity import (
    AccessTier,
    BuyerContext,
    DealRequest,
    DealResponse,
    DealType,
)
from ...models.sgp import ApprovalRecord

logger = logging.getLogger(__name__)

_VALID_UNKNOWN_POLICIES = {"block", "warn", "allow"}


class RequestDealInput(BaseModel):
    """Input schema for deal request tool."""

    product_id: str = Field(
        ...,
        description="Product ID to request deal for",
    )
    deal_type: str = Field(
        default="PD",
        description="Deal type: 'PG' (Programmatic Guaranteed), 'PD' (Preferred Deal), 'PA' (Private Auction)",
    )
    impressions: int | None = Field(
        default=None,
        description="Requested impression volume (required for PG deals)",
        ge=0,
    )
    flight_start: str | None = Field(
        default=None,
        description="Deal start date (YYYY-MM-DD)",
    )
    flight_end: str | None = Field(
        default=None,
        description="Deal end date (YYYY-MM-DD)",
    )
    target_cpm: float | None = Field(
        default=None,
        description="Target CPM for negotiation (agency/advertiser tier only)",
        ge=0,
    )
    audience_plan: AudiencePlan | None = Field(
        default=None,
        description=(
            "Typed AudiencePlan threaded onto the seller-bound deal "
            "request (proposal §5.1). None on legacy paths."
        ),
    )


class RequestDealTool(BaseTool):
    """Request a Deal ID from a seller for programmatic activation.

    This tool creates programmatic deals that can be activated in traditional
    DSP platforms (The Trade Desk, DV360, Amazon DSP, etc.).

    Deal Types:
    - PG (Programmatic Guaranteed): Fixed price, guaranteed impressions
    - PD (Preferred Deal): Fixed price, non-guaranteed first-look
    - PA (Private Auction): Auction with floor price, invited buyers

    The returned Deal ID can be entered into any DSP platform's
    Private Marketplace section for activation.
    """

    name: str = "request_deal"
    description: str = """Request a Deal ID from seller for programmatic activation.
Returns a Deal ID that can be used in DSP platforms (TTD, DV360, Amazon DSP).

Args:
    product_id: Product ID to request deal for
    deal_type: 'PG' (guaranteed), 'PD' (preferred), or 'PA' (private auction)
    impressions: Volume (required for PG deals)
    flight_start: Start date (YYYY-MM-DD)
    flight_end: End date (YYYY-MM-DD)
    target_cpm: Target price for negotiation (agency/advertiser only)

Returns:
    Deal ID and activation instructions for DSP platforms."""

    args_schema: type[BaseModel] = RequestDealInput
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
            sgp_client: Optional IAB Diligence Platform client. When provided
                and ``sgp_enforce`` is True, the seller's IAB buyer-agent
                approval is verified before a Deal ID is generated.
            sgp_enforce: When True, block the deal request unless SGP
                returns ``iabBuyerAgentApproval=true`` for the seller.
            sgp_unknown_policy: How to treat vendors absent from the
                buyer's SGP portfolio (HTTP 404). One of ``block``,
                ``warn``, ``allow``.
        """
        super().__init__(**kwargs)
        self._client = client
        self._buyer_context = buyer_context
        self._sgp_client = sgp_client
        self._sgp_enforce = sgp_enforce
        if sgp_unknown_policy not in _VALID_UNKNOWN_POLICIES:
            raise ValueError(
                f"Invalid sgp_unknown_policy '{sgp_unknown_policy}'. "
                f"Must be one of: {', '.join(sorted(_VALID_UNKNOWN_POLICIES))}"
            )
        self._sgp_unknown_policy = sgp_unknown_policy

    def _run(
        self,
        product_id: str,
        deal_type: str = "PD",
        impressions: int | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
        target_cpm: float | None = None,
        audience_plan: AudiencePlan | None = None,
    ) -> str:
        """Synchronous wrapper for async deal request."""
        return run_async(
            self._arun(
                product_id=product_id,
                deal_type=deal_type,
                impressions=impressions,
                flight_start=flight_start,
                flight_end=flight_end,
                target_cpm=target_cpm,
                audience_plan=audience_plan,
            )
        )

    async def _arun(
        self,
        product_id: str,
        deal_type: str = "PD",
        impressions: int | None = None,
        flight_start: str | None = None,
        flight_end: str | None = None,
        target_cpm: float | None = None,
        audience_plan: AudiencePlan | None = None,
    ) -> str:
        """Request a deal ID from the seller."""
        try:
            # Validate deal type
            try:
                deal_type_enum = DealType(deal_type.upper())
            except ValueError:
                return f"Invalid deal type '{deal_type}'. Use 'PG', 'PD', or 'PA'."

            # Validate PG requirements
            if deal_type_enum == DealType.PROGRAMMATIC_GUARANTEED and not impressions:
                return "Programmatic Guaranteed (PG) deals require an impressions volume."

            # Check negotiation eligibility (buyer tier)
            tier = self._buyer_context.identity.get_access_tier()
            if target_cpm and not self._buyer_context.can_negotiate():
                return (
                    f"Price negotiation requires Agency or Advertiser tier (current: {tier.value})"
                )

            # Get product details first
            product_result = await self._client.get_product(product_id)
            if not product_result.success:
                return f"Error getting product: {product_result.error}"

            product = product_result.data
            if not product:
                return f"Product {product_id} not found."
              
            # Build the seller-bound DealRequest payload so the plan
            # rides on the wire (proposal §5.2 / §5.3 / bead ar-ts30 §18).
            # We construct the payload even when audience_plan is None so
            # tests can inspect a single payload object regardless of
            # whether audience targeting was supplied.
            deal_request_payload = self.build_deal_request_payload(
                product_id=product_id,
                deal_type=deal_type,
                impressions=impressions,
                flight_start=flight_start,
                flight_end=flight_end,
                target_cpm=target_cpm,
                audience_plan=audience_plan,
            )
            # Stash the payload + plan on the tool instance so tests and
            # observability code can inspect what crossed the boundary
            # without parsing the formatted text. Mirrors the §5 wire
            # additions to QuoteRequest / DealBookingRequest.
            self._last_deal_request = deal_request_payload
            self._last_audience_plan = audience_plan

            # IAB Diligence Platform approval gate — must pass before a Deal ID is issued.
            gate_error, approval_banner = await self._check_sgp_approval(product)
            if gate_error:
                return gate_error

            # Calculate pricing
            deal_response = self._create_deal_response(
                product=product,
                deal_type=deal_type_enum,
                impressions=impressions,
                flight_start=flight_start,
                flight_end=flight_end,
                target_cpm=target_cpm,
                audience_plan=audience_plan,
            )

            formatted = self._format_deal_response(deal_response, audience_plan)
            if approval_banner:
                formatted = f"{approval_banner}\n{formatted}"
            return formatted

        except (OSError, ValueError, RuntimeError) as e:
            return f"Error requesting deal: {e}"

    async def _check_sgp_approval(
        self, product: dict
    ) -> tuple[str | None, str | None]:
        """Gate a deal request against IAB Diligence Platform approval.

        Returns ``(error_message, banner)``:
          * ``error_message`` is non-None when the deal must be refused.
          * ``banner`` is a one-line note prepended to a successful deal
            response (e.g. "warn" policy, unknown vendor proceeding).

        When ``sgp_client`` is None or ``sgp_enforce`` is False, the gate
        is skipped entirely.
        """
        if self._sgp_client is None or not self._sgp_enforce:
            return None, None

        raw_domain = extract_product_domain(product)
        if not raw_domain:
            return (
                "Deal blocked: cannot determine seller domain for IAB "
                "Diligence Platform approval check. Add a seller_url / "
                "publisher_domain field to the product, or disable SGP_ENFORCE.",
                None,
            )

        domain = self._sgp_client.normalize_domain(raw_domain) or raw_domain

        try:
            approvals = await self._sgp_client.check_approvals([raw_domain])
        except SGPClientError as exc:
            logger.warning(
                "IAB Diligence Platform lookup failed for %s during deal request", domain,
                exc_info=True,
            )
            # Fail closed — enforcement is on, so we must not issue a Deal ID
            # when the privacy gate cannot be evaluated.
            return (
                f"Deal blocked: IAB Diligence Platform lookup failed for {domain} "
                f"({exc}). Retry once the SGP service is reachable.",
                None,
            )

        record: ApprovalRecord | None = approvals.get(domain)

        if record is None:
            if self._sgp_unknown_policy == "allow":
                return None, f"SGP: {domain} not in SGP portfolio — allowed by policy."
            if self._sgp_unknown_policy == "warn":
                return None, (
                    f"SGP WARNING: {domain} is not in your SGP portfolio. "
                    f"Onboard and approve this vendor in IAB Diligence Platform "
                    f"to suppress this warning."
                )
            return (
                f"Deal blocked: {domain} is not in your IAB Diligence Platform "
                f"portfolio. Onboard and approve the vendor in SGP before "
                f"requesting a Deal ID.",
                None,
            )

        if not record.iab_buyer_agent_approval:
            return (
                f"Deal blocked: {record.company_name or domain} does not carry "
                f"the IAB buyer-agent approval flag in IAB Diligence Platform. "
                f"Update the vendor's approval in SGP and retry.",
                None,
            )

        approved_at = (
            record.iab_buyer_agent_approved_at.isoformat()
            if record.iab_buyer_agent_approved_at
            else "date unknown"
        )
        banner = (
            f"SGP: ✓ {record.company_name or domain} approved for IAB "
            f"buyer-agent purchases (since {approved_at})."
        )
        return None, banner

    def _create_deal_response(
        self,
        product: dict,
        deal_type: DealType,
        impressions: int | None,
        flight_start: str | None,
        flight_end: str | None,
        target_cpm: float | None,
        audience_plan: AudiencePlan | None = None,
    ) -> DealResponse:
        """Create a deal response with calculated pricing.

        Uses the centralized PricingCalculator and deal ID generator
        from ad_buyer.booking to avoid duplicated logic.
        """
        tier = self._buyer_context.identity.get_access_tier()
        discount = self._buyer_context.identity.get_discount_percentage()
        base_price = product.get("basePrice", product.get("price", 20.0))

        if not isinstance(base_price, (int, float)):
            base_price = 20.0

        calculator = PricingCalculator()
        pricing = calculator.calculate(
            base_price=base_price,
            tier=tier,
            tier_discount=discount,
            volume=impressions,
            target_cpm=target_cpm,
            can_negotiate=self._buyer_context.can_negotiate(),
            negotiation_enabled=product.get("negotiation_enabled", False),
        )

        identity = self._buyer_context.identity
        deal_id = generate_deal_id(
            product_id=product.get("id", "unknown"),
            identity_seed=identity.agency_id or identity.seat_id or "public",
        )

        now = datetime.now(timezone.utc)
        if not flight_start:
            flight_start = now.strftime("%Y-%m-%d")
        if not flight_end:
            flight_end = (now + timedelta(days=30)).strftime("%Y-%m-%d")

        activation_instructions = {
            "ttd": f"The Trade Desk > Inventory > Private Marketplace > Add Deal ID: {deal_id}",
            "dv360": f"Display & Video 360 > Inventory > My Inventory > New > Deal ID: {deal_id}",
            "amazon": f"Amazon DSP > Private Marketplace > Deals > Add Deal: {deal_id}",
            "xandr": f"Xandr > Inventory > Deals > Create Deal with ID: {deal_id}",
            "yahoo": f"Yahoo DSP > Inventory > Private Marketplace > Enter Deal ID: {deal_id}",
        }

        return DealResponse(
            deal_id=deal_id,
            product_id=product.get("id", "unknown"),
            product_name=product.get("name", "Unknown Product"),
            deal_type=deal_type,
            price=round(pricing.final_price, 2),
            original_price=round(pricing.base_price, 2),
            discount_applied=round(discount, 1),
            access_tier=tier,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            activation_instructions=activation_instructions,
            expires_at=(now + timedelta(days=7)).strftime("%Y-%m-%d"),
        )

    def build_deal_request_payload(
        self,
        product_id: str,
        deal_type: str,
        impressions: int | None,
        flight_start: str | None,
        flight_end: str | None,
        target_cpm: float | None,
        audience_plan: AudiencePlan | None,
        notes: str | None = None,
    ) -> DealRequest:
        """Construct the typed seller-bound payload for the deal request.

        The Audience Planner step on BuyerDealFlow puts an ``AudiencePlan``
        on flow state; this helper materializes the wire-shape ``DealRequest``
        so the plan rides on the seller-bound payload (proposal §5.2 / §5.3
        + bead ar-ts30 §18). Tests assert the plan survives this boundary.
        """

        try:
            deal_type_enum = DealType(deal_type.upper())
        except ValueError:
            deal_type_enum = DealType.PREFERRED_DEAL

        return DealRequest(
            product_id=product_id,
            deal_type=deal_type_enum,
            impressions=impressions,
            flight_start=flight_start,
            flight_end=flight_end,
            target_cpm=target_cpm,
            notes=notes,
            audience_plan=audience_plan,
        )

    def _format_deal_response(
        self,
        deal: DealResponse,
        audience_plan: AudiencePlan | None = None,
    ) -> str:
        """Format deal response for output."""
        deal_type_names = {
            DealType.PROGRAMMATIC_GUARANTEED: "Programmatic Guaranteed (PG)",
            DealType.PREFERRED_DEAL: "Preferred Deal (PD)",
            DealType.PRIVATE_AUCTION: "Private Auction (PA)",
        }

        output_lines = [
            "=" * 60,
            "DEAL CREATED SUCCESSFULLY",
            "=" * 60,
            "",
            f"Deal ID: {deal.deal_id}",
            "",
            "Deal Details",
            "-" * 30,
            f"Product: {deal.product_name}",
            f"Product ID: {deal.product_id}",
            f"Deal Type: {deal_type_names.get(deal.deal_type, deal.deal_type.value)}",
            f"Flight: {deal.flight_start} to {deal.flight_end}",
        ]

        if deal.impressions:
            output_lines.append(f"Impressions: {deal.impressions:,}")

        # Surface the AudiencePlan id when one rode on the request -- gives
        # the human reviewer (and audit trail) a stable handle linking
        # buyer state to seller-side records (proposal §5.1 step 2).
        if audience_plan is not None:
            output_lines.append(
                f"Audience Plan ID: {audience_plan.audience_plan_id}"
            )

        output_lines.extend(
            [
                "",
                "Pricing",
                "-" * 30,
                f"Original CPM: ${deal.original_price:.2f}",
                f"Your Tier: {deal.access_tier.value.upper()} ({deal.discount_applied}% discount)",
                f"Final CPM: ${deal.price:.2f}",
            ]
        )

        if deal.impressions:
            total_cost = (deal.price / 1000) * deal.impressions
            output_lines.append(f"Estimated Total: ${total_cost:,.2f}")

        output_lines.extend(
            [
                "",
                "Activation Instructions",
                "-" * 30,
            ]
        )

        for platform, instruction in deal.activation_instructions.items():
            output_lines.append(f"• {platform.upper()}: {instruction}")

        output_lines.extend(
            [
                "",
                "-" * 30,
                f"Deal expires: {deal.expires_at}",
                "",
                "Copy the Deal ID above and enter it in your DSP's",
                "Private Marketplace or Inventory section.",
                "=" * 60,
            ]
        )

        return "\n".join(output_lines)
