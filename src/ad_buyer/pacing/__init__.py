# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Budget pacing and reallocation engine for campaign automation.

EXPERIMENTAL / NOT YET OPERATIONAL: not wired into any live path.
See ad_buyer.pacing.engine for details. Retained for future
development.

Design intent (not yet live): real-time budget pacing analysis,
deviation detection, and cross-channel reallocation recommendations.

2C: Budget Pacing & Reallocation.
"""

from .engine import (
    BudgetPacingEngine,
    PacingAlert,
    PacingAlertLevel,
    PacingConfig,
    ReallocationProposal,
)

__all__ = [
    "BudgetPacingEngine",
    "PacingAlert",
    "PacingAlertLevel",
    "PacingConfig",
    "ReallocationProposal",
]
