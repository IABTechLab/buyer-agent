# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Storage layer for the Ad Buyer System.

Exports two complementary persistence APIs:

- The legacy synchronous :class:`DealStore` (sqlite3-backed) plus its
  singleton factory and schema utilities, used by CrewAI agents that
  cannot safely cross an event loop.
- The pluggable async :class:`StorageBackend` abstraction and its
  :func:`get_storage_backend` factory, which selects SQLite (default),
  Redis, or a Postgres+Redis hybrid at startup based on configuration.

The pluggable backend mirrors the seller-agent's storage architecture and
provides an async key/value interface plus higher-level domain helpers.
Callers may migrate from direct DealStore access to the pluggable
backend incrementally; the two coexist.
"""

from typing import Optional

from .base import StorageBackend
from .deal_store import DealStore
from .factory import get_storage_backend
from .schema import SCHEMA_VERSION, create_tables, initialize_schema

_store_instance: DealStore | None = None


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
    "StorageBackend",
    "get_deal_store",
    "get_storage_backend",
    "SCHEMA_VERSION",
    "create_tables",
    "initialize_schema",
]
