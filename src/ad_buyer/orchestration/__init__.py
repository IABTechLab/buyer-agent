# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Orchestration modules for campaign automation.

This package contains:
- MultiSellerOrchestrator: Coordinates multi-seller deal discovery,
  parallel quote collection, evaluation, and booking.
"""

from .audience_degradation import (
    CannotFulfillPlan,
    DegradationLog,
    DegradationLogEntry,
    SellerAudienceCapabilities,
    degrade_plan_for_seller,
    synthesize_capabilities_from_unsupported,
)
from .multi_seller import (
    DealParams,
    DealSelection,
    InventoryRequirements,
    MultiSellerOrchestrator,
    OrchestrationResult,
    SellerQuoteResult,
)

__all__ = [
    "CannotFulfillPlan",
    "DealParams",
    "DealSelection",
    "DegradationLog",
    "DegradationLogEntry",
    "InventoryRequirements",
    "MultiSellerOrchestrator",
    "OrchestrationResult",
    "SellerAudienceCapabilities",
    "SellerQuoteResult",
    "degrade_plan_for_seller",
    "synthesize_capabilities_from_unsupported",
]
