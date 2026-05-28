# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Abstract base class and result types for SSP deal import connectors.

All SSP connectors (PubMatic, Magnite, Index Exchange, etc.) implement
the SSPConnector interface defined here.  The base class establishes:

- SSPFetchResult: result container for a fetch operation
- SSPConnector: abstract base class all connectors must implement
- SSPConnectionError, SSPAuthError, SSPRateLimitError: connector error types

Usage pattern (same as CSV import, just a different data source):

    connector = PubMaticConnector(api_token=os.environ["PUBMATIC_API_TOKEN"])
    if not connector.is_configured():
        raise RuntimeError("PubMatic connector is not configured")

    result = connector.fetch_deals(status="active")
    for deal in result.deals:
        deal_id = store.save_deal(**deal)
        store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source=connector.import_source,
            import_date=today_iso,
        )
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SSPFetchResult:
    """Result of an SSP deal fetch operation.

    Mirrors ImportResult from deal_import.py, extended with SSP-specific
    fields (ssp_name, raw_response_count).

    Attributes:
        deals: Successfully normalized deals as dicts ready for
            ``DealStore.save_deal()``.
        errors: Human-readable error messages for deals that could
            not be normalized.
        total_fetched: Total number of raw deals received from the SSP
            API (before normalization).
        successful: Number of deals successfully normalized.
        failed: Number of deals that failed normalization.
        skipped: Number of deals skipped (e.g. duplicates, filtered out).
        ssp_name: Human-readable SSP name (e.g. "PubMatic").
        raw_response_count: Number of deal records in the raw API
            response, before any filtering or normalization.
    """

    deals: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_fetched: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    ssp_name: str = ""
    raw_response_count: int = 0


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class SSPConnectionError(Exception):
    """Raised when the connector cannot reach the SSP API.

    Examples: network timeout, DNS failure, HTTP 5xx response.

    Attributes:
        status_code: Optional HTTP status code from the SSP response.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SSPAuthError(Exception):
    """Raised when SSP API credentials are invalid or expired.

    Examples: HTTP 401 Unauthorized, HTTP 403 Forbidden.

    Attributes:
        status_code: Optional HTTP status code from the SSP response.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SSPRateLimitError(Exception):
    """Raised when the SSP API returns a rate limit response.

    Examples: HTTP 429 Too Many Requests.

    Attributes:
        retry_after: Optional number of seconds before the caller
            should retry, parsed from the Retry-After response header.
    """

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class SSPConnector(ABC):
    """Abstract base class for all SSP deal import connectors.

    Subclasses implement ``fetch_deals()`` and ``_normalize_deal()`` for their
    specific SSP API format.  The base class provides:

    - ``is_configured()``: check whether required env vars are present
    - ``get_required_config()``: list of env var names the connector needs

    The flow for subclasses mirrors the CSV import pattern:

    1. ``fetch_deals(**kwargs)`` calls the SSP API and returns an
       ``SSPFetchResult`` whose ``deals`` list is ready for
       ``DealStore.save_deal(**deal)``.
    2. ``_normalize_deal(raw_deal)`` maps a single SSP API response
       object to a DealStore kwargs dict.

    All normalized deals must include at minimum:
    - ``seller_url``: SSP API base URL
    - ``product_id``: SSP deal ID or package ID
    - ``deal_type``: one of PG, PD, PA, OPEN_AUCTION, UPFRONT, SCATTER
    - ``status``: set to ``"imported"`` for newly fetched deals
    - ``seller_deal_id``: deal ID as it appears in OpenRTB bid requests
    - ``display_name``: human-readable deal name
    - ``seller_org``: SSP name (e.g. "PubMatic")
    - ``seller_type``: always ``"SSP"`` for SSP connectors

    Example implementation::

        class PubMaticConnector(SSPConnector):

            @property
            def ssp_name(self) -> str:
                return "PubMatic"

            @property
            def import_source(self) -> str:
                return "PUBMATIC"

            def get_required_config(self) -> list[str]:
                return ["PUBMATIC_API_TOKEN", "PUBMATIC_SEAT_ID"]

            def fetch_deals(self, **kwargs) -> SSPFetchResult:
                ...

            def _normalize_deal(self, raw_deal: dict) -> dict:
                ...
    """

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement these
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def ssp_name(self) -> str:
        """Human-readable SSP name (e.g. "PubMatic", "Magnite")."""
        ...

    @property
    @abstractmethod
    def import_source(self) -> str:
        """Import source tag written to portfolio_metadata.

        Used to identify where a deal came from in the deal library
        (e.g. "PUBMATIC", "MAGNITE", "INDEX_EXCHANGE").
        """
        ...

    @abstractmethod
    def fetch_deals(self, **kwargs: Any) -> SSPFetchResult:
        """Fetch deals from the SSP and return normalized DealStore dicts.

        kwargs are connector-specific filter parameters such as status,
        date range, deal type, seat ID, etc.  The returned
        ``SSPFetchResult.deals`` list contains dicts ready for
        ``DealStore.save_deal(**deal)``.

        Args:
            **kwargs: Connector-specific filter/query parameters.

        Returns:
            SSPFetchResult with normalized deals, error messages, and
            counts.

        Raises:
            SSPConnectionError: If the SSP API cannot be reached.
            SSPAuthError: If credentials are invalid or expired.
            SSPRateLimitError: If the SSP API returns a rate-limit error.
        """
        ...

    @abstractmethod
    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        """Map a single raw SSP API response object to DealStore kwargs.

        Must set at minimum:
        - ``seller_url``, ``product_id``, ``deal_type``, ``status``
        - ``seller_deal_id``, ``display_name``, ``seller_org``
        - ``seller_type`` = ``"SSP"``

        Args:
            raw_deal: A single deal dict from the SSP API response.

        Returns:
            Dict matching ``DealStore.save_deal()`` keyword arguments.

        Raises:
            KeyError: If a required field is missing from ``raw_deal``.
            ValueError: If a field value cannot be mapped to the schema.
        """
        ...

    # ------------------------------------------------------------------
    # Concrete helpers — available to all subclasses
    # ------------------------------------------------------------------

    def get_required_config(self) -> list[str]:
        """Return the list of environment variable names this connector needs.

        Subclasses should override this to declare their required env vars.
        The default implementation returns an empty list (no requirements).

        Returns:
            List of environment variable names (e.g.
            ["PUBMATIC_API_TOKEN", "PUBMATIC_SEAT_ID"]).
        """
        return []

    def is_configured(self) -> bool:
        """Return True if all required environment variables are set.

        Checks each variable returned by ``get_required_config()`` against
        ``os.environ``.  A variable is considered set if it is present
        and non-empty.

        Returns:
            True if all required env vars are set and non-empty,
            False otherwise.
        """
        for var in self.get_required_config():
            if not os.environ.get(var):
                return False
        return True
