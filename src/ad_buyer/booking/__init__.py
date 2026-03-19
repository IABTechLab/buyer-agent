# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Booking modules for campaign deal orchestration.

This package contains:
- QuoteNormalizer: Normalizes quotes from different sellers for comparison.
"""

from .quote_normalizer import NormalizedQuote, QuoteNormalizer, SupplyPathInfo

__all__ = ["NormalizedQuote", "QuoteNormalizer", "SupplyPathInfo"]
