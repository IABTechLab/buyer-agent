# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""InstantiateDealFromTemplateTool for DealJockey.

Creates a deal from a stored deal template by:
1. Reading the template from storage
2. Applying optional overrides (pricing, targeting, flight dates)
3. Calling the seller POST /api/v1/deals/from-template via internal booking modules
4. Storing the new deal in the portfolio on success
5. Emitting a deal.template_created event

On rejection (e.g. max_cpm < seller floor), returns rejection details
including the seller's minimum price so the agent can adjust.

See: buyer-te6b.2.8 (InstantiateDealFromTemplateTool [dj7])
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...events.helpers import emit_event_sync
from ...events.models import EventType
from ...storage.deal_store import DealStore

logger = logging.getLogger(__name__)


# -- Result dataclass --------------------------------------------------------


@dataclass
class TemplateInstantiationResult:
    """Result of a template-based deal instantiation.

    Attributes:
        success: Whether the deal was created successfully.
        deal_id: The new deal's ID (None on failure).
        deal_record: Dict with deal details (None on failure).
        rejection: Rejection details from seller (None if not rejected).
        event_emitted: Whether the deal.template_created event was emitted.
        errors: List of error messages (empty on success).
    """

    success: bool
    deal_id: Optional[str] = None
    deal_record: Optional[dict[str, Any]] = None
    rejection: Optional[dict[str, Any]] = None
    event_emitted: bool = False
    errors: list[str] = field(default_factory=list)


# -- Seller API stub ---------------------------------------------------------


def _call_seller_template_api(
    seller_url: str,
    template_id: str,
    buyer_params: dict[str, Any],
) -> dict[str, Any]:
    """Call seller POST /api/v1/deals/from-template.

    This is a stub that will be replaced by a real HTTP call when the
    seller-side template API is implemented.  For now, it simulates
    a successful response.

    Args:
        seller_url: Base URL of the seller API.
        template_id: Template ID to instantiate.
        buyer_params: Buyer parameters including max_cpm, impressions, etc.

    Returns:
        Dict with seller response (success or rejection).
    """
    # Default stub: simulate success
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
            "seller_url": seller_url,
        },
    }


# -- Core instantiation function --------------------------------------------


def instantiate_deal_from_template(
    *,
    deal_store: DealStore,
    template_id: str,
    advertiser_id: str,
    seller_url: str,
    seller_api_fn: Optional[Callable] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> TemplateInstantiationResult:
    """Instantiate a deal from a stored template.

    Reads the template from storage, applies optional overrides,
    calls the seller API (or stub), and on success stores the new
    deal in the portfolio.

    Args:
        deal_store: DealStore instance for persistence.
        template_id: ID of the deal template to instantiate.
        advertiser_id: Advertiser to associate the deal with.
        seller_url: Seller API base URL.
        seller_api_fn: Callable for the seller API (default: stub).
        overrides: Optional dict of field overrides (max_cpm, targeting,
            flight_start, flight_end, impressions).

    Returns:
        TemplateInstantiationResult with success/failure details.
    """
    if seller_api_fn is None:
        seller_api_fn = _call_seller_template_api

    if overrides is None:
        overrides = {}

    # Step 1: Read template from storage
    template = deal_store.get_deal_template(template_id)
    if template is None:
        return TemplateInstantiationResult(
            success=False,
            errors=[f"Template not found: {template_id}"],
        )

    # Step 2: Build buyer parameters from template + overrides
    buyer_params = _build_buyer_params(template, overrides)

    # Step 3: Call seller API
    try:
        seller_response = seller_api_fn(seller_url, template_id, buyer_params)
    except Exception as exc:
        logger.error("Seller API call failed for template %s: %s", template_id, exc)
        return TemplateInstantiationResult(
            success=False,
            errors=[f"Seller API error: {exc}"],
        )

    # Step 4: Handle rejection
    if not seller_response.get("success"):
        rejection = {
            "reason": seller_response.get("reason", "Unknown rejection"),
            "seller_floor_cpm": seller_response.get("seller_floor_cpm"),
            "buyer_max_cpm": seller_response.get("buyer_max_cpm"),
        }
        return TemplateInstantiationResult(
            success=False,
            rejection=rejection,
        )

    # Step 5: Store the deal in portfolio
    deal_data = seller_response["deal"]
    deal_id = deal_store.save_deal(
        seller_url=seller_url,
        product_id=deal_data.get("product_id", f"prod-from-{template_id}"),
        product_name=deal_data.get("product_name", ""),
        deal_type=deal_data.get("deal_type", template.get("deal_type_pref", "PD")),
        status="draft",
        seller_deal_id=deal_data.get("seller_deal_id"),
        price=deal_data.get("price"),
        impressions=deal_data.get("impressions"),
        flight_start=deal_data.get("flight_start") or buyer_params.get("flight_start"),
        flight_end=deal_data.get("flight_end") or buyer_params.get("flight_end"),
        metadata=json.dumps({
            "template_id": template_id,
            "template_name": template.get("name"),
            "overrides_applied": overrides if overrides else {},
        }),
    )

    # Step 6: Store portfolio metadata
    deal_store.save_portfolio_metadata(
        deal_id=deal_id,
        import_source="TEMPLATE",
        advertiser_id=advertiser_id,
        agency_id=template.get("agency_id"),
        tags=json.dumps([f"template:{template_id}"]),
    )

    # Step 7: Emit deal.template_created event
    event_emitted = False
    try:
        emit_event_sync(
            EventType.DEAL_TEMPLATE_CREATED,
            deal_id=deal_id,
            flow_type="deal_template_instantiation",
            payload={
                "template_id": template_id,
                "template_name": template.get("name"),
                "advertiser_id": advertiser_id,
                "deal_type": deal_data.get("deal_type"),
                "price": deal_data.get("price"),
                "overrides": overrides if overrides else {},
            },
        )
        event_emitted = True
    except Exception as exc:
        # Fail-open: event emission should not block deal creation
        logger.warning("Failed to emit deal.template_created event: %s", exc)

    # Step 8: Build deal record for the response
    deal_record = {
        "deal_id": deal_id,
        "template_id": template_id,
        "template_name": template.get("name"),
        "deal_type": deal_data.get("deal_type", template.get("deal_type_pref", "PD")),
        "price": deal_data.get("price"),
        "impressions": deal_data.get("impressions"),
        "flight_start": deal_data.get("flight_start") or buyer_params.get("flight_start"),
        "flight_end": deal_data.get("flight_end") or buyer_params.get("flight_end"),
        "seller_deal_id": deal_data.get("seller_deal_id"),
        "seller_url": seller_url,
        "advertiser_id": advertiser_id,
        "status": "draft",
    }

    return TemplateInstantiationResult(
        success=True,
        deal_id=deal_id,
        deal_record=deal_record,
        event_emitted=event_emitted,
    )


# -- Param building helpers --------------------------------------------------


def _build_buyer_params(
    template: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Build buyer parameters from template defaults + overrides.

    Template fields provide defaults; overrides take precedence.

    Args:
        template: Deal template dict from storage.
        overrides: User-provided overrides.

    Returns:
        Dict of buyer parameters for the seller API call.
    """
    # Start with template defaults
    params: dict[str, Any] = {
        "deal_type": template.get("deal_type_pref", "PD"),
        "max_cpm": template.get("max_cpm"),
        "impressions": template.get("min_impressions"),
    }

    # Flight dates from template default_flight_days
    default_flight_days = template.get("default_flight_days")
    if default_flight_days:
        params["flight_start"] = datetime.now().strftime("%Y-%m-%d")
        params["flight_end"] = (
            datetime.now() + timedelta(days=default_flight_days)
        ).strftime("%Y-%m-%d")

    # Targeting defaults from template
    targeting_defaults = template.get("targeting_defaults")
    if targeting_defaults:
        if isinstance(targeting_defaults, str):
            try:
                targeting_defaults = json.loads(targeting_defaults)
            except (json.JSONDecodeError, TypeError):
                targeting_defaults = {}
        params["targeting"] = targeting_defaults

    # Inventory types from template
    inventory_types = template.get("inventory_types")
    if inventory_types:
        if isinstance(inventory_types, str):
            try:
                inventory_types = json.loads(inventory_types)
            except (json.JSONDecodeError, TypeError):
                inventory_types = []
        params["inventory_types"] = inventory_types

    # Apply overrides
    if "max_cpm" in overrides:
        params["max_cpm"] = overrides["max_cpm"]
    if "impressions" in overrides:
        params["impressions"] = overrides["impressions"]
    if "flight_start" in overrides:
        params["flight_start"] = overrides["flight_start"]
    if "flight_end" in overrides:
        params["flight_end"] = overrides["flight_end"]
    if "deal_type" in overrides:
        params["deal_type"] = overrides["deal_type"]
    if "targeting" in overrides:
        # Merge targeting: override values take precedence
        base_targeting = params.get("targeting", {})
        if isinstance(base_targeting, dict):
            base_targeting.update(overrides["targeting"])
            params["targeting"] = base_targeting
        else:
            params["targeting"] = overrides["targeting"]

    return params


# -- CrewAI tool wrapper ------------------------------------------------------


class InstantiateDealFromTemplateInput(BaseModel):
    """Input schema for InstantiateDealFromTemplateTool."""

    params_json: str = Field(
        ...,
        description=(
            "JSON string with instantiation parameters. "
            "Required: template_id, advertiser_id, seller_url. "
            "Optional: overrides (dict with max_cpm, targeting, "
            "flight_start, flight_end, impressions, deal_type)."
        ),
    )


class InstantiateDealFromTemplateTool(BaseTool):
    """Create a deal from a stored deal template.

    Reads a deal template from storage, applies optional overrides
    (pricing, targeting, flight dates), and calls the seller
    POST /api/v1/deals/from-template endpoint via internal booking
    modules.  On success, stores the new deal in the portfolio and
    emits a deal.template_created event.

    On rejection (e.g. max_cpm below seller floor), returns rejection
    details including the seller's minimum acceptable price.
    """

    name: str = "instantiate_deal_from_template"
    description: str = (
        "Create a new deal from a stored deal template. "
        "Accepts template_id, advertiser_id, seller_url, and optional overrides "
        "(max_cpm, targeting, flight_start, flight_end, impressions). "
        "Returns the new deal record on success, or rejection details "
        "(including seller floor price) on failure."
    )
    args_schema: type[BaseModel] = InstantiateDealFromTemplateInput
    deal_store: Any = Field(exclude=True)

    def _run(self, params_json: str) -> str:
        """Execute template-based deal creation.

        Args:
            params_json: JSON string with template_id, advertiser_id,
                seller_url, and optional overrides.

        Returns:
            Human-readable result string.
        """
        # Parse JSON input
        try:
            params = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        # Validate required fields
        template_id = params.get("template_id")
        advertiser_id = params.get("advertiser_id")
        seller_url = params.get("seller_url")

        missing = []
        if not template_id:
            missing.append("template_id")
        if not advertiser_id:
            missing.append("advertiser_id")
        if not seller_url:
            missing.append("seller_url")

        if missing:
            return f"Error: Missing required fields: {', '.join(missing)}"

        overrides = params.get("overrides")

        # Call core function
        result = instantiate_deal_from_template(
            deal_store=self.deal_store,
            template_id=template_id,
            advertiser_id=advertiser_id,
            seller_url=seller_url,
            seller_api_fn=_call_seller_template_api,
            overrides=overrides,
        )

        if result.success:
            return self._format_success(result)
        elif result.rejection:
            return self._format_rejection(result)
        else:
            error_list = "\n".join(f"  - {e}" for e in result.errors)
            return f"Error: Template instantiation failed:\n{error_list}"

    def _format_success(self, result: TemplateInstantiationResult) -> str:
        """Format a successful instantiation result."""
        rec = result.deal_record
        lines = [
            "Deal created successfully from template.",
            "",
            f"  Deal ID: {rec['deal_id']}",
            f"  Template: {rec.get('template_name', rec.get('template_id'))}",
            f"  Deal Type: {rec['deal_type']}",
            f"  Status: {rec['status']}",
        ]

        if rec.get("price") is not None:
            lines.append(f"  Price (CPM): ${rec['price']:.2f}")
        if rec.get("impressions") is not None:
            lines.append(f"  Impressions: {rec['impressions']:,}")
        if rec.get("flight_start"):
            lines.append(f"  Flight Start: {rec['flight_start']}")
        if rec.get("flight_end"):
            lines.append(f"  Flight End: {rec['flight_end']}")
        if rec.get("seller_deal_id"):
            lines.append(f"  Seller Deal ID: {rec['seller_deal_id']}")

        lines.append(f"  Advertiser: {rec.get('advertiser_id', 'N/A')}")
        lines.append(f"  Event Emitted: {'Yes' if result.event_emitted else 'No'}")

        return "\n".join(lines)

    def _format_rejection(self, result: TemplateInstantiationResult) -> str:
        """Format a rejection result with seller floor price."""
        rej = result.rejection
        lines = [
            "Deal rejected by seller.",
            "",
            f"  Reason: {rej.get('reason', 'Unknown')}",
        ]

        if rej.get("seller_floor_cpm") is not None:
            lines.append(f"  Seller Floor CPM: ${rej['seller_floor_cpm']:.2f}")
        if rej.get("buyer_max_cpm") is not None:
            lines.append(f"  Your Max CPM: ${rej['buyer_max_cpm']:.2f}")

        lines.append("")
        lines.append("  Tip: Increase max_cpm in overrides or adjust the template.")

        return "\n".join(lines)
