# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal state persistence layer.

Exports the DealStore class, singleton factory, and schema utilities for
SQLite-backed storage of deals, negotiations, bookings, jobs, and status
transitions.
"""

from typing import Optional

from .deal_store import DealStore
from .schema import SCHEMA_VERSION, create_tables, initialize_schema

_store_instance: Optional[DealStore] = None


def get_deal_store(database_url: str = "sqlite:///./ad_buyer.db") -> DealStore:
    """Return a module-level singleton DealStore, creating it on first call.

    Args:
        database_url: SQLite connection string. Only used on the first
            invocation; subsequent calls return the cached instance.

    Returns:
        Connected DealStore singleton.
    """
    global _store_instance
    if _store_instance is None:
        _store_instance = DealStore(database_url)
        _store_instance.connect()
    return _store_instance


__all__ = [
    "DealStore",
    "get_deal_store",
    "SCHEMA_VERSION",
    "create_tables",
    "initialize_schema",
]
