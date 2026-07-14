# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deterministic spend-ceiling guard for money-committing paths.

Nothing on the buyer's booking path previously compared the final price
to the campaign's limits: ``max_cpm`` reached the selection LLM only as
prose and the seller only as an advisory search filter, so a seller
quoting far above the buyer's ceiling would still be issued a Deal ID.

``enforce_spend_ceiling`` closes that hole. It is pure, deterministic
code — no LLM involvement, no configuration flag to disable it — and is
called at every point where the buyer commits money:

* ``RequestDealTool._create_deal_response`` (before a Deal ID is minted)
* ``DealBookingFlow._execute_bookings`` (before booked lines are created)

Note: ``MultiSellerOrchestrator`` already enforces its own bounds
(``evaluate_and_rank`` filters quotes by ``max_cpm``; ``select_and_book``
skips quotes whose minimum spend exceeds the remaining budget), so it is
intentionally not routed through this guard.

Fail-open policy: when a limit is None (the caller never supplied a
max_cpm or budget), the corresponding check is skipped and a warning is
logged. This is an explicit choice to preserve current demo behavior for
callers that never configure limits; it is NOT a bypass for configured
limits — a supplied limit is always enforced.
"""

import logging

logger = logging.getLogger(__name__)

__all__ = ["SpendCeilingExceeded", "enforce_spend_ceiling"]


class SpendCeilingExceeded(Exception):
    """Raised when a deal or booking would breach a spend limit.

    Deliberately does NOT subclass ValueError/RuntimeError so that broad
    ``except (OSError, ValueError, RuntimeError)`` handlers on the tool
    paths cannot swallow it by accident — call sites must handle the
    rejection explicitly.

    Attributes:
        final_cpm: Computed final CPM that was checked (or None).
        max_cpm: CPM ceiling that was breached (or None).
        total_cost: Computed total cost that was checked (or None).
        budget: Budget ceiling that was breached (or None).
    """

    def __init__(
        self,
        message: str,
        *,
        final_cpm: float | None = None,
        max_cpm: float | None = None,
        total_cost: float | None = None,
        budget: float | None = None,
    ) -> None:
        super().__init__(message)
        self.final_cpm = final_cpm
        self.max_cpm = max_cpm
        self.total_cost = total_cost
        self.budget = budget


def enforce_spend_ceiling(
    final_cpm: float | None = None,
    total_cost: float | None = None,
    max_cpm: float | None = None,
    budget: float | None = None,
) -> None:
    """Enforce CPM and budget ceilings before money is committed.

    Pure deterministic guard: compares actuals against limits and raises
    when a limit would be breached. At-or-under a limit is allowed.

    Args:
        final_cpm: The computed final CPM about to be committed.
        total_cost: The total cost about to be committed.
        max_cpm: The campaign's maximum acceptable CPM (limit).
        budget: The campaign's budget (limit).

    Raises:
        SpendCeilingExceeded: When final_cpm > max_cpm or
            total_cost > budget (both comparisons only run when both
            sides are present).
    """
    if max_cpm is None and budget is None:
        # Fail-open (explicit choice): no limits were supplied, so there
        # is nothing to enforce. Allow the spend but log a warning so
        # unbounded money paths are visible in logs. This preserves
        # current demo behavior for callers without configured limits.
        logger.warning(
            "Spend ceiling check skipped: no max_cpm or budget limit supplied "
            "(final_cpm=%s, total_cost=%s). Spend is unbounded.",
            final_cpm,
            total_cost,
        )
        return

    if max_cpm is not None and final_cpm is not None and final_cpm > max_cpm:
        raise SpendCeilingExceeded(
            f"Final CPM ${final_cpm:.2f} exceeds max CPM ceiling ${max_cpm:.2f}",
            final_cpm=final_cpm,
            max_cpm=max_cpm,
            total_cost=total_cost,
            budget=budget,
        )

    if budget is not None and total_cost is not None and total_cost > budget:
        raise SpendCeilingExceeded(
            f"Total cost ${total_cost:,.2f} exceeds budget ${budget:,.2f}",
            final_cpm=final_cpm,
            max_cpm=max_cpm,
            total_cost=total_cost,
            budget=budget,
        )
