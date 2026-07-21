# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Storage layer for the Ad Buyer System.

Exports the synchronous :class:`DealStore` (sqlite3-backed) plus its
singleton factory and schema utilities, used by CrewAI agents that
cannot safely cross an event loop, along with the aggregate stores
extracted from DealStore (EP-2.4).
"""

from .booking_record_store import BookingRecordStore
from .creative_asset_store import CreativeAssetStore
from .deal_activation_store import DealActivationStore
from .deal_event_store import DealEventStore
from .deal_store import DealStore
from .deal_template_store import DealTemplateStore
from .job_store import JobStore
from .negotiation_store import NegotiationStore
from .performance_cache_store import PerformanceCacheStore
from .portfolio_metadata_store import PortfolioMetadataStore
from .schema import SCHEMA_VERSION, create_tables, initialize_schema
from .status_transition_store import StatusTransitionStore
from .supply_path_template_store import SupplyPathTemplateStore

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
    "get_deal_store",
    "SCHEMA_VERSION",
    "create_tables",
    "initialize_schema",
    # Aggregate stores extracted from DealStore (EP-2.4)
    "BookingRecordStore",
    "CreativeAssetStore",
    "DealActivationStore",
    "DealEventStore",
    "DealTemplateStore",
    "JobStore",
    "NegotiationStore",
    "PerformanceCacheStore",
    "PortfolioMetadataStore",
    "StatusTransitionStore",
    "SupplyPathTemplateStore",
]
