# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal state persistence layer (SQLite).

Exports:
    DealStore: The main store class for deal lifecycle, negotiations,
        bookings, and job tracking.
    get_deal_store: Singleton factory that returns a connected DealStore
        using the application's database_url setting.
"""

from typing import Optional

from .deal_store import DealStore
from .schema import (
    CURRENT_SCHEMA_VERSION,
    apply_migrations,
    create_tables,
    get_schema_version,
)

__all__ = [
    "DealStore",
    "get_deal_store",
    "CURRENT_SCHEMA_VERSION",
    "apply_migrations",
    "create_tables",
    "get_schema_version",
]

_store: Optional[DealStore] = None


def get_deal_store() -> DealStore:
    """Return a singleton DealStore connected to the configured database.

    Uses ``settings.database_url`` from the application config.
    Creates and connects the store on first call; subsequent calls
    return the same instance.

    Returns:
        A connected DealStore instance.
    """
    global _store
    if _store is None:
        from ..config.settings import settings

        _store = DealStore(settings.database_url)
        _store.connect()
    return _store
