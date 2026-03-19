# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Booking modules for deal creation and pricing.

This package consolidates deal-booking logic that was previously
duplicated across unified_client.py, request_deal.py, and get_pricing.py.

Public API:
    PricingCalculator - Calculate tiered and volume-discounted pricing
    PricingResult - Result dataclass from pricing calculations
    generate_deal_id - Generate unique deal IDs
    QuoteFlowClient - Quote-then-book flow for deal creation
    TemplateFlowClient - Template-based booking (stub)
"""

from .deal_id import generate_deal_id
from .pricing import PricingCalculator, PricingResult
from .quote_flow import QuoteFlowClient
from .template_flow import TemplateFlowClient

__all__ = [
    "PricingCalculator",
    "PricingResult",
    "generate_deal_id",
    "QuoteFlowClient",
    "TemplateFlowClient",
]
