# Author: Agent Range
# Donated to IAB Tech Lab

"""Anti-corruption boundary mappers: shared contract types <-> buyer models.

EP-12.1 adopts the shared contract library ``iab_agentic_primitives`` at the
buyer's HTTP wire edge ONLY. Internal code keeps using the buyer's own
``ad_buyer.models`` types; the client serialize/deserialize layer speaks the
shared protocol envelopes and primitives, and these mappers translate between
the two at the boundary.

Design rules:

- Money crosses the wire as the shared ``Money`` (exact integer micros, FD-11).
  The buyer's internal models still use ``float`` dollars, so every money field
  is converted at the boundary (``_money_from_float`` / ``_float_from_money``).
- Money-mutating requests carry a required ``idempotency_key`` (FD-12). The
  buyer mints one per request when the caller does not supply it.
- The buyer's internal ``deal_type`` is a free string; the shared contract types
  it as ``DealType`` (PG/PD/PA). Linear-TV pseudo deal types (scatter/upfront/
  opportunistic) have no equivalent in the shared v0.1.0 contract — which models
  linear TV via ``media_type`` + ``linear_tv`` params, not ``deal_type`` — so
  they map to the closest shared analog (see ``_to_wire_deal_type``).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from iab_agentic_primitives.primitives import (
    BuyerIdentity as WireBuyerIdentity,
)
from iab_agentic_primitives.primitives import (
    CancellationTerms as WireCancellationTerms,
)
from iab_agentic_primitives.primitives import (
    DealType,
    MediaType,
    Money,
    Quote,
    QuoteAvailability,
    QuotePricing,
    QuoteTerms,
)
from iab_agentic_primitives.primitives import (
    LinearTVParams as WireLinearTVParams,
)
from iab_agentic_primitives.primitives import (
    LinearTVQuoteDetails as WireLinearTVQuoteDetails,
)
from iab_agentic_primitives.protocol import (
    QuoteRequest as WireQuoteRequest,
)
from iab_agentic_primitives.protocol import (
    QuoteResponse as WireQuoteResponse,
)

from ..models.deals import (
    AvailabilityInfo,
    BuyerIdentityPayload,
    PricingInfo,
    ProductInfo,
    QuoteRequest,
    QuoteResponse,
    TermsInfo,
)
from ..models.linear_tv import CancellationTerms, LinearTVParams, LinearTVQuoteDetails

# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

_MICROS_PER_UNIT = 1_000_000


def _money_from_float(value: float | None, currency: str = "USD") -> Money | None:
    """Convert an internal float dollar amount to the shared Money (micros)."""
    if value is None:
        return None
    return Money(amount_micros=int(round(value * _MICROS_PER_UNIT)), currency=currency)


def _float_from_money(money: Money | None) -> float | None:
    """Convert a shared Money back to an internal float dollar amount."""
    if money is None:
        return None
    return money.amount_micros / _MICROS_PER_UNIT


def _currency_of(*monies: Money | None, default: str = "USD") -> str:
    """First present Money's currency; the internal models carry one currency."""
    for money in monies:
        if money is not None:
            return money.currency
    return default


def _to_date(value: str | None) -> date | None:
    """Parse an internal ISO date string into a ``date`` for the wire model."""
    if value is None:
        return None
    return date.fromisoformat(value)


def _date_to_iso(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _to_wire_deal_type(value: str) -> DealType:
    """Map the buyer's free-string deal_type to the typed shared ``DealType``.

    PG/PD/PA map directly. Linear-TV pseudo deal types have no shared
    equivalent (the shared contract expresses linear TV via media_type +
    linear_tv params) and fall back to Preferred Deal, the non-guaranteed
    analog. See module docstring.
    """
    try:
        return DealType(value)
    except ValueError:
        return DealType.PREFERRED_DEAL


# ---------------------------------------------------------------------------
# Shared sub-object mappers
# ---------------------------------------------------------------------------


def _to_wire_buyer_identity(bi: BuyerIdentityPayload | None) -> WireBuyerIdentity | None:
    if bi is None:
        return None
    return WireBuyerIdentity(
        seat_id=bi.seat_id,
        agency_id=bi.agency_id,
        advertiser_id=bi.advertiser_id,
        dsp_platform=bi.dsp_platform,
    )


def _to_wire_linear_tv_params(ltv: LinearTVParams | None) -> WireLinearTVParams | None:
    if ltv is None:
        return None
    return WireLinearTVParams(
        target_demo=ltv.target_demo,
        grps_requested=ltv.grps_requested,
        dayparts=ltv.dayparts,
        networks=ltv.networks,
        dmas=ltv.dmas,
        spot_length=ltv.spot_length,
        target_cpp=_money_from_float(ltv.target_cpp),
        measurement_currency=ltv.measurement_currency,
        rotation=ltv.rotation,
    )


def _from_wire_pricing(pricing: QuotePricing) -> PricingInfo:
    currency = _currency_of(
        pricing.final_cpm, pricing.base_cpm, pricing.final_cpp, pricing.base_cpp
    )
    return PricingInfo(
        base_cpm=_float_from_money(pricing.base_cpm),
        tier_discount_pct=pricing.tier_discount_pct,
        volume_discount_pct=pricing.volume_discount_pct,
        final_cpm=_float_from_money(pricing.final_cpm),
        currency=currency,
        pricing_model=pricing.pricing_model.value,
        rationale=pricing.rationale,
        base_cpp=_float_from_money(pricing.base_cpp),
        final_cpp=_float_from_money(pricing.final_cpp),
    )


def _from_wire_terms(terms: QuoteTerms) -> TermsInfo:
    return TermsInfo(
        impressions=terms.impressions,
        flight_start=_date_to_iso(terms.flight_start),
        flight_end=_date_to_iso(terms.flight_end),
        guaranteed=terms.guaranteed,
        grps=terms.grps,
        guaranteed_grps=terms.guaranteed_grps,
        target_demo=terms.target_demo,
    )


def _from_wire_availability(avail: QuoteAvailability | None) -> AvailabilityInfo | None:
    if avail is None:
        return None
    return AvailabilityInfo(
        inventory_available=avail.inventory_available,
        estimated_fill_rate=avail.estimated_fill_rate,
        competing_demand=avail.competing_demand,
    )


def _from_wire_cancellation_terms(
    terms: WireCancellationTerms | None,
) -> CancellationTerms | None:
    if terms is None:
        return None
    return CancellationTerms(
        notice_days=terms.notice_days,
        cancellable_pct=terms.cancellable_pct,
        deadline=_date_to_iso(terms.deadline),
        force_majeure=terms.force_majeure,
    )


def _from_wire_linear_tv_details(
    details: WireLinearTVQuoteDetails | None,
) -> LinearTVQuoteDetails | None:
    if details is None:
        return None
    return LinearTVQuoteDetails(
        target_demo=details.target_demo,
        estimated_grps=details.estimated_grps,
        estimated_rating=details.estimated_rating,
        cpp=_float_from_money(details.cpp),
        dayparts=details.dayparts,
        networks=details.networks,
        spots_per_week=details.spots_per_week,
        total_spots=details.total_spots,
        spot_length=details.spot_length,
        measurement_currency=details.measurement_currency,
        audience_estimate=details.audience_estimate,
        cancellation_terms=_from_wire_cancellation_terms(details.cancellation_terms),
        makegood_policy=details.makegood_policy,
    )


# ---------------------------------------------------------------------------
# Quote surface
# ---------------------------------------------------------------------------


def to_wire_quote_request(
    req: QuoteRequest, *, idempotency_key: str | None = None
) -> WireQuoteRequest:
    """Build the shared ``QuoteRequest`` envelope from the buyer's model."""
    media_type = MediaType(req.media_type)
    wire_linear_tv = (
        _to_wire_linear_tv_params(req.linear_tv)
        if media_type is MediaType.LINEAR_TV
        else None
    )
    return WireQuoteRequest(
        idempotency_key=idempotency_key or uuid4().hex,
        product_id=req.product_id,
        deal_type=_to_wire_deal_type(req.deal_type),
        impressions=req.impressions,
        flight_start=_to_date(req.flight_start),
        flight_end=_to_date(req.flight_end),
        target_cpm=_money_from_float(req.target_cpm),
        buyer_identity=_to_wire_buyer_identity(req.buyer_identity),
        agent_url=req.agent_url,
        media_type=media_type,
        linear_tv=wire_linear_tv,
        audience_plan=(
            req.audience_plan.model_dump(mode="json") if req.audience_plan else None
        ),
    )


def from_wire_quote_response(wire: WireQuoteResponse) -> QuoteResponse:
    """Map the shared ``QuoteResponse`` envelope to the buyer's model."""
    quote: Quote = wire.quote
    return QuoteResponse(
        quote_id=quote.quote_id,
        status=quote.status.value,
        product=ProductInfo(
            product_id=quote.product.product_id,
            name=quote.product.name,
            inventory_type=quote.product.inventory_type,
        ),
        pricing=_from_wire_pricing(quote.pricing),
        terms=_from_wire_terms(quote.terms),
        availability=_from_wire_availability(quote.availability),
        buyer_tier=quote.buyer_tier.value,
        expires_at=_dt_to_iso(quote.expires_at),
        seller_id=quote.seller_id,
        created_at=_dt_to_iso(quote.created_at),
        media_type=quote.media_type.value,
        linear_tv=_from_wire_linear_tv_details(quote.linear_tv),
    )


__all__ = [
    "to_wire_quote_request",
    "from_wire_quote_response",
]
