# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite-backed negotiation round persistence.

Extracted from ``DealStore`` as part of the EP-2.4 god-class
split.  Operates on the ``negotiation_rounds`` table, which is created by
``schema.initialize_schema`` during ``DealStore.connect()``.  Instances share
the owning DealStore's SQLite connection and lock, so all writes remain
serialized under a single lock exactly as before the split.
"""

import sqlite3
import threading
from typing import Any


class NegotiationStore:
    """Store for per-deal negotiation rounds.

    Args:
        conn: Active SQLite connection (owned by the composing DealStore).
        lock: Shared lock serializing access to the connection.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def save_negotiation_round(
        self,
        *,
        deal_id: str,
        proposal_id: str,
        round_number: int,
        buyer_price: float,
        seller_price: float,
        action: str,
        rationale: str = "",
    ) -> int:
        """Record a negotiation round.

        Args:
            deal_id: FK to deals.
            proposal_id: Seller's proposal ID.
            round_number: Sequential round number.
            buyer_price: Buyer's offered price.
            seller_price: Seller's asking price.
            action: counter, accept, reject, final_offer.
            rationale: Explanation for the action.

        Returns:
            The auto-generated row ID.
        """
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO negotiation_rounds
                   (deal_id, proposal_id, round_number, buyer_price,
                    seller_price, action, rationale)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    deal_id,
                    proposal_id,
                    round_number,
                    buyer_price,
                    seller_price,
                    action,
                    rationale,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_negotiation_history(self, deal_id: str) -> list[dict[str, Any]]:
        """Get all negotiation rounds for a deal, ordered by round number.

        Args:
            deal_id: The deal to query.

        Returns:
            List of round dicts.
        """
        with self._lock:
            cursor = self._conn.execute(
                """SELECT * FROM negotiation_rounds
                   WHERE deal_id = ?
                   ORDER BY round_number ASC""",
                (deal_id,),
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]
