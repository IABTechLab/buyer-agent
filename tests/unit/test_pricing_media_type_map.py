"""_book_approved must emit shared-enum media types on the quote/deal wire.

Run #15 (bead ar-9iw5): DealParams carried the discovery vocabulary
("display"), and QuoteRequest validation rejected it — the quote leg died
client-side before any seller was called.
"""

from ad_buyer.flows.deal_booking_flow import (
    _CHANNEL_MEDIA_TYPE_MAP,
    _PRICING_MEDIA_TYPE_MAP,
)
from ad_buyer.models.deals import QuoteRequest


def test_every_channel_media_type_translates_to_a_valid_pricing_media_type():
    for channel, discovery_type in _CHANNEL_MEDIA_TYPE_MAP.items():
        pricing_type = _PRICING_MEDIA_TYPE_MAP.get(discovery_type, "digital")
        request = QuoteRequest(
            product_id="p1",
            deal_type="PD",
            impressions=1_000_000,
            flight_start="2026-08-01",
            flight_end="2026-08-31",
            media_type=pricing_type,
        )
        assert request.media_type == pricing_type, channel


def test_display_maps_to_digital_the_run_15_regression():
    assert _PRICING_MEDIA_TYPE_MAP["display"] == "digital"
    request = QuoteRequest(
        product_id="p1",
        deal_type="PD",
        impressions=1,
        flight_start="2026-08-01",
        flight_end="2026-08-31",
        media_type=_PRICING_MEDIA_TYPE_MAP["display"],
    )
    assert request.media_type == "digital"


def test_unknown_discovery_types_fall_back_to_digital():
    assert _PRICING_MEDIA_TYPE_MAP.get("smoke-signals", "digital") == "digital"
