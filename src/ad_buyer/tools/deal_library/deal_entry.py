# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Manual deal entry tool for DealLibrary.

Validates and prepares structured deal input for saving to the deal library.
This is a pure validation/preparation tool -- it does NOT interact with
DealStore directly. The caller (DealLibrary agent or a higher-level flow)
handles persistence and event emission.

Usage:
    entry = ManualDealEntry(
        display_name="ESPN Sports PMP",
        seller_url="https://espn.seller.example.com",
        deal_type="PD",
        media_type="DIGITAL",
    )
    result = create_manual_deal(entry)
    if result.success:
        deal_id = store.save_deal(**result.deal_data)
        # emit EventType.DEAL_IMPORTED with import_source="MANUAL"
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# -- Valid enum values --------------------------------------------------------

VALID_DEAL_TYPES = {"PG", "PD", "PA", "OPEN_AUCTION", "UPFRONT", "SCATTER"}
VALID_MEDIA_TYPES = {"DIGITAL", "CTV", "LINEAR_TV", "AUDIO", "DOOH"}
VALID_SELLER_TYPES = {"PUBLISHER", "SSP", "DSP", "INTERMEDIARY"}
VALID_PRICE_MODELS = {"CPM", "CPP", "FLAT", "HYBRID"}
VALID_STATUSES = {"draft", "active", "paused"}


# -- Input model --------------------------------------------------------------


class ManualDealEntry(BaseModel):
    """Input for manually creating a single deal.

    Required fields: display_name and seller_url.
    All other fields have defaults or are optional.
    """

    # Required
    display_name: str = Field(
        ...,
        description="Human-readable name for the deal",
    )
    seller_url: str = Field(
        ...,
        description="Seller endpoint URL",
    )

    # Required with defaults
    product_id: str = Field(
        default="manual-entry",
        description="Product identifier (defaults to 'manual-entry' for manual deals)",
    )
    deal_type: str = Field(
        default="PD",
        description="Deal type: PG, PD, PA, OPEN_AUCTION, UPFRONT, or SCATTER",
    )
    status: str = Field(
        default="draft",
        description="Initial deal status: draft, active, or paused",
    )

    # Optional counterparty
    seller_deal_id: str | None = Field(
        default=None,
        description="Seller-assigned deal ID",
    )
    seller_org: str | None = Field(
        default=None,
        description="Seller organization name",
    )
    seller_domain: str | None = Field(
        default=None,
        description="Seller domain (e.g., nbcuniversal.com)",
    )
    seller_type: str | None = Field(
        default=None,
        description="Seller type: PUBLISHER, SSP, DSP, or INTERMEDIARY",
    )
    buyer_org: str | None = Field(
        default=None,
        description="Buyer organization name",
    )
    buyer_id: str | None = Field(
        default=None,
        description="Buyer identifier",
    )

    # Optional pricing
    price: float | None = Field(
        default=None,
        description="Deal price (CPM or flat rate depending on price_model)",
    )
    fixed_price_cpm: float | None = Field(
        default=None,
        description="Fixed CPM price",
    )
    bid_floor_cpm: float | None = Field(
        default=None,
        description="Bid floor CPM for auction-based deals",
    )
    price_model: str | None = Field(
        default=None,
        description="Pricing model: CPM, CPP, FLAT, or HYBRID",
    )
    currency: str = Field(
        default="USD",
        description="Currency code (default: USD)",
    )

    # Optional inventory
    media_type: str | None = Field(
        default=None,
        description="Media type: DIGITAL, CTV, LINEAR_TV, AUDIO, or DOOH",
    )
    impressions: int | None = Field(
        default=None,
        description="Contracted impression volume",
    )

    # Optional dates
    flight_start: str | None = Field(
        default=None,
        description="Flight start date (ISO 8601, e.g. 2026-04-01)",
    )
    flight_end: str | None = Field(
        default=None,
        description="Flight end date (ISO 8601, e.g. 2026-06-30)",
    )

    # Optional metadata
    description: str | None = Field(
        default=None,
        description="Free-text deal description",
    )
    advertiser_id: str | None = Field(
        default=None,
        description="Advertiser identifier for portfolio tracking",
    )
    tags: list[str] | None = Field(
        default=None,
        description="Tags for categorization (e.g., ['premium', 'sports'])",
    )


# -- Output dataclass ----------------------------------------------------------


@dataclass
class DealEntryResult:
    """Result of a manual deal entry validation.

    Attributes:
        success: Whether validation passed.
        deal_data: Validated deal dict ready for DealStore.save_deal(), or None on failure.
        metadata: Portfolio metadata dict (tags, advertiser_id, import_source), or None on failure.
        errors: List of validation error messages (empty on success).
    """

    success: bool
    deal_data: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


# -- Validation & preparation -------------------------------------------------


def _validate_entry(entry: ManualDealEntry) -> list[str]:
    """Validate a ManualDealEntry and return a list of error messages.

    Returns an empty list if the entry is valid.
    """
    errors: list[str] = []

    # display_name must not be empty or whitespace-only
    if not entry.display_name or not entry.display_name.strip():
        errors.append("display_name must not be empty")

    # deal_type must be in valid set
    if entry.deal_type not in VALID_DEAL_TYPES:
        errors.append(
            f"Invalid deal_type '{entry.deal_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_DEAL_TYPES))}"
        )

    # status must be in valid set
    if entry.status not in VALID_STATUSES:
        errors.append(
            f"Invalid status '{entry.status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
        )

    # media_type validation (only if provided)
    if entry.media_type is not None and entry.media_type not in VALID_MEDIA_TYPES:
        errors.append(
            f"Invalid media_type '{entry.media_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_MEDIA_TYPES))}"
        )

    # seller_type validation (only if provided)
    if entry.seller_type is not None and entry.seller_type not in VALID_SELLER_TYPES:
        errors.append(
            f"Invalid seller_type '{entry.seller_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_SELLER_TYPES))}"
        )

    # price_model validation (only if provided)
    if entry.price_model is not None and entry.price_model not in VALID_PRICE_MODELS:
        errors.append(
            f"Invalid price_model '{entry.price_model}'. "
            f"Must be one of: {', '.join(sorted(VALID_PRICE_MODELS))}"
        )

    # Flight date ordering: end must not be before start
    if entry.flight_start is not None and entry.flight_end is not None:
        if entry.flight_end < entry.flight_start:
            errors.append(
                f"flight_end ({entry.flight_end}) must not be before "
                f"flight_start ({entry.flight_start})"
            )

    return errors


def _build_deal_data(entry: ManualDealEntry) -> dict[str, Any]:
    """Build a deal dict compatible with DealStore.save_deal() kwargs.

    Maps ManualDealEntry fields to save_deal() parameters.  V2 deal library
    fields are passed as top-level keys so they are stored in dedicated
    columns and survive the save/load roundtrip (instead of being buried
    in the metadata JSON blob where inspect_deal cannot find them).
    """
    # Build the deal dict matching DealStore.save_deal() parameters.
    # V1 core fields:
    deal_data: dict[str, Any] = {
        "seller_url": entry.seller_url,
        "product_id": entry.product_id,
        "product_name": entry.display_name,
        "deal_type": entry.deal_type,
        "status": entry.status,
        "seller_deal_id": entry.seller_deal_id,
        "price": entry.price,
        "impressions": entry.impressions,
        "flight_start": entry.flight_start,
        "flight_end": entry.flight_end,
    }

    # V2 intrinsic fields -- passed as top-level kwargs to save_deal()
    # so they are stored in their dedicated columns.
    deal_data["display_name"] = entry.display_name
    deal_data["currency"] = entry.currency

    if entry.seller_org is not None:
        deal_data["seller_org"] = entry.seller_org
    if entry.seller_domain is not None:
        deal_data["seller_domain"] = entry.seller_domain
    if entry.seller_type is not None:
        deal_data["seller_type"] = entry.seller_type
    if entry.buyer_org is not None:
        deal_data["buyer_org"] = entry.buyer_org
    if entry.buyer_id is not None:
        deal_data["buyer_id"] = entry.buyer_id
    if entry.price_model is not None:
        deal_data["price_model"] = entry.price_model
    if entry.fixed_price_cpm is not None:
        deal_data["fixed_price_cpm"] = entry.fixed_price_cpm
    if entry.bid_floor_cpm is not None:
        deal_data["bid_floor_cpm"] = entry.bid_floor_cpm
    if entry.media_type is not None:
        deal_data["media_type"] = entry.media_type
    if entry.description is not None:
        deal_data["description"] = entry.description

    return deal_data


def _build_portfolio_metadata(entry: ManualDealEntry) -> dict[str, Any]:
    """Build portfolio metadata dict for the portfolio_metadata table.

    This maps to the extrinsic portfolio_metadata columns:
    import_source, tags, advertiser_id.
    """
    return {
        "import_source": "MANUAL",
        "tags": entry.tags,
        "advertiser_id": entry.advertiser_id,
    }


def create_manual_deal(entry: ManualDealEntry) -> DealEntryResult:
    """Validate and prepare a manual deal entry for saving.

    This is a pure validation/preparation function -- it does NOT
    call DealStore. The caller saves the deal and emits
    EventType.DEAL_IMPORTED with import_source="MANUAL".

    Args:
        entry: Validated ManualDealEntry input model.

    Returns:
        DealEntryResult with success/failure, deal_data dict
        ready for save_deal(), portfolio metadata, and any
        validation errors.
    """
    errors = _validate_entry(entry)

    if errors:
        return DealEntryResult(
            success=False,
            deal_data=None,
            metadata=None,
            errors=errors,
        )

    deal_data = _build_deal_data(entry)
    metadata = _build_portfolio_metadata(entry)

    return DealEntryResult(
        success=True,
        deal_data=deal_data,
        metadata=metadata,
        errors=[],
    )


# -- CrewAI tool wrapper -------------------------------------------------------


class ManualDealEntryToolInput(BaseModel):
    """Input schema for the ManualDealEntryTool CrewAI wrapper."""

    deal_params: str = Field(
        ...,
        description=(
            "JSON string containing deal parameters. "
            "Required fields: display_name, seller_url. "
            "Optional: product_id, deal_type, status, seller_deal_id, "
            "seller_org, seller_domain, seller_type, buyer_org, buyer_id, "
            "price, fixed_price_cpm, bid_floor_cpm, price_model, currency, "
            "media_type, impressions, flight_start, flight_end, description, "
            "advertiser_id, tags."
        ),
    )


class ManualDealEntryTool(BaseTool):
    """Create a single deal from structured input.

    Accepts deal parameters as a JSON string, validates them, and
    returns a human-readable summary of the prepared deal or
    validation errors. The tool does NOT persist the deal; the
    caller is responsible for saving via DealStore and emitting
    the DEAL_IMPORTED event.
    """

    name: str = "manual_deal_entry"
    description: str = (
        "Create a single deal from structured input for the deal portfolio. "
        "Accepts a JSON string with deal parameters (display_name, seller_url required). "
        "Returns a summary of the validated deal or validation errors."
    )
    args_schema: type[BaseModel] = ManualDealEntryToolInput

    def _run(self, deal_params: str) -> str:
        """Validate deal parameters and return a human-readable result.

        Args:
            deal_params: JSON string with deal parameters.

        Returns:
            Human-readable summary of the created deal or error messages.
        """
        # Parse JSON input
        try:
            params = json.loads(deal_params)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        # Build ManualDealEntry from parsed params
        try:
            entry = ManualDealEntry(**params)
        except (ValueError, TypeError) as exc:
            return f"Error: Invalid deal parameters -- {exc}"

        # Validate and prepare
        result = create_manual_deal(entry)

        if not result.success:
            error_list = "\n".join(f"  - {e}" for e in result.errors)
            return f"Error: Deal validation failed:\n{error_list}"

        # Format success response
        return self._format_success(result)

    def _format_success(self, result: DealEntryResult) -> str:
        """Format a successful deal entry result as a human-readable string."""
        data = result.deal_data
        meta = result.metadata

        lines = [
            "Deal created successfully.",
            "",
            f"  Name: {data['product_name']}",
            f"  Seller URL: {data['seller_url']}",
            f"  Deal Type: {data['deal_type']}",
            f"  Status: {data['status']}",
        ]

        if data.get("seller_deal_id"):
            lines.append(f"  Seller Deal ID: {data['seller_deal_id']}")
        if data.get("price") is not None:
            lines.append(f"  Price: {data['price']}")
        if data.get("impressions") is not None:
            lines.append(f"  Impressions: {data['impressions']:,}")
        if data.get("flight_start"):
            lines.append(f"  Flight Start: {data['flight_start']}")
        if data.get("flight_end"):
            lines.append(f"  Flight End: {data['flight_end']}")

        lines.append(f"  Import Source: {meta['import_source']}")

        if meta.get("advertiser_id"):
            lines.append(f"  Advertiser: {meta['advertiser_id']}")
        if meta.get("tags"):
            lines.append(f"  Tags: {', '.join(meta['tags'])}")

        return "\n".join(lines)
