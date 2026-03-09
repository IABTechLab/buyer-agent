# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Media Kit Discovery Client for browsing seller inventory packages.

Consumes the seller's media kit REST endpoints:
  GET  /media-kit                      — overview (name, featured, all packages)
  GET  /media-kit/packages             — list packages (optional layer/featured filters)
  GET  /media-kit/packages/{id}        — single package detail
  POST /media-kit/search               — keyword search with optional auth context

Authenticated requests (with API key) receive richer data: exact pricing,
placements, audience segments, and negotiation flags.
"""

import asyncio
import logging
from typing import Optional

import httpx

from .models import (
    MediaKit,
    MediaKitError,
    PackageDetail,
    PackageSummary,
    PlacementDetail,
    SearchFilter,
)

logger = logging.getLogger(__name__)


class MediaKitClient:
    """Client for consuming seller media kits.

    Args:
        api_key: Optional API key for authenticated access (exact pricing).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        """Build request headers, including API key if configured."""
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _normalize_url(self, seller_url: str) -> str:
        """Strip trailing slash from seller URL."""
        return seller_url.rstrip("/")

    def _parse_package_summary(self, data: dict, seller_url: str) -> PackageSummary:
        """Parse a dict into a PackageSummary, attaching seller_url."""
        return PackageSummary(
            package_id=data.get("package_id", ""),
            name=data.get("name", ""),
            description=data.get("description"),
            ad_formats=data.get("ad_formats", []),
            device_types=data.get("device_types", []),
            cat=data.get("cat", []),
            cattax=data.get("cattax", 2),
            geo_targets=data.get("geo_targets", []),
            tags=data.get("tags", []),
            price_range=data.get("price_range", ""),
            rate_type=data.get("rate_type", "cpm"),
            is_featured=data.get("is_featured", False),
            seller_url=seller_url,
        )

    def _parse_package_detail(self, data: dict, seller_url: str) -> PackageDetail:
        """Parse a dict into a PackageDetail with placement and pricing info."""
        placements = [
            PlacementDetail(
                product_id=p.get("product_id", ""),
                product_name=p.get("product_name", ""),
                ad_formats=p.get("ad_formats", []),
                device_types=p.get("device_types", []),
                weight=p.get("weight", 1.0),
            )
            for p in data.get("placements", [])
        ]
        return PackageDetail(
            package_id=data.get("package_id", ""),
            name=data.get("name", ""),
            description=data.get("description"),
            ad_formats=data.get("ad_formats", []),
            device_types=data.get("device_types", []),
            cat=data.get("cat", []),
            cattax=data.get("cattax", 2),
            geo_targets=data.get("geo_targets", []),
            tags=data.get("tags", []),
            price_range=data.get("price_range", ""),
            rate_type=data.get("rate_type", "cpm"),
            is_featured=data.get("is_featured", False),
            seller_url=seller_url,
            exact_price=data.get("exact_price"),
            floor_price=data.get("floor_price"),
            currency=data.get("currency", "USD"),
            placements=placements,
            audience_segment_ids=data.get("audience_segment_ids", []),
            negotiation_enabled=data.get("negotiation_enabled", False),
            volume_discounts_available=data.get("volume_discounts_available", False),
        )

    def _parse_package(self, data: dict, seller_url: str) -> PackageSummary | PackageDetail:
        """Parse a package dict, returning PackageDetail if auth fields present."""
        if "exact_price" in data:
            return self._parse_package_detail(data, seller_url)
        return self._parse_package_summary(data, seller_url)

    async def _handle_response(
        self,
        response: httpx.Response,
        seller_url: str,
    ) -> dict:
        """Check response status and return JSON, or raise MediaKitError."""
        if response.status_code >= 400:
            raise MediaKitError(
                message=f"HTTP {response.status_code} from {seller_url}",
                seller_url=seller_url,
                status_code=response.status_code,
            )
        return response.json()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_media_kit(self, seller_url: str) -> MediaKit:
        """Fetch a seller's media kit overview.

        Calls GET /media-kit on the seller.

        Args:
            seller_url: Base URL of the seller (e.g. "http://localhost:8001").

        Returns:
            MediaKit with seller name, featured packages, and all packages.

        Raises:
            MediaKitError: On network or HTTP errors.
        """
        base = self._normalize_url(seller_url)
        headers = self._build_headers()
        try:
            resp = await self._http.get(f"{base}/media-kit", headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise MediaKitError(
                message=f"Failed to connect to {seller_url}: {exc}",
                seller_url=seller_url,
            ) from exc

        data = await self._handle_response(resp, seller_url)

        featured = [self._parse_package_summary(p, seller_url) for p in data.get("featured", [])]
        all_packages = [self._parse_package_summary(p, seller_url) for p in data.get("all_packages", [])]

        return MediaKit(
            seller_url=seller_url,
            seller_name=data.get("seller_name", ""),
            total_packages=data.get("total_packages", len(all_packages)),
            featured=featured,
            all_packages=all_packages,
        )

    async def list_packages(
        self,
        seller_url: str,
        layer: Optional[str] = None,
        featured_only: bool = False,
    ) -> list[PackageSummary]:
        """List available packages from a seller.

        Calls GET /media-kit/packages on the seller.

        Args:
            seller_url: Base URL of the seller.
            layer: Filter by package layer ("synced", "curated", "dynamic").
            featured_only: Only return featured packages.

        Returns:
            List of PackageSummary objects.

        Raises:
            MediaKitError: On network or HTTP errors.
        """
        base = self._normalize_url(seller_url)
        headers = self._build_headers()
        params: dict[str, str | bool] = {}
        if layer:
            params["layer"] = layer
        if featured_only:
            params["featured_only"] = featured_only

        try:
            resp = await self._http.get(f"{base}/media-kit/packages", headers=headers, params=params)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise MediaKitError(
                message=f"Failed to connect to {seller_url}: {exc}",
                seller_url=seller_url,
            ) from exc

        data = await self._handle_response(resp, seller_url)
        return [self._parse_package_summary(p, seller_url) for p in data.get("packages", [])]

    async def get_package(
        self,
        seller_url: str,
        package_id: str,
    ) -> PackageSummary | PackageDetail:
        """Get a single package by ID.

        Calls GET /media-kit/packages/{package_id}.
        Returns PackageDetail if the response includes authenticated fields
        (exact_price, placements, etc.), otherwise PackageSummary.

        Args:
            seller_url: Base URL of the seller.
            package_id: The package ID to retrieve.

        Returns:
            PackageSummary (public) or PackageDetail (authenticated).

        Raises:
            MediaKitError: On network, HTTP, or 404 errors.
        """
        base = self._normalize_url(seller_url)
        headers = self._build_headers()

        try:
            resp = await self._http.get(f"{base}/media-kit/packages/{package_id}", headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise MediaKitError(
                message=f"Failed to connect to {seller_url}: {exc}",
                seller_url=seller_url,
            ) from exc

        data = await self._handle_response(resp, seller_url)
        return self._parse_package(data, seller_url)

    async def search_packages(
        self,
        seller_url: str,
        query: str,
        filters: Optional[SearchFilter] = None,
    ) -> list[PackageSummary]:
        """Search a seller's media kit packages.

        Calls POST /media-kit/search on the seller.

        Args:
            seller_url: Base URL of the seller.
            query: Search query string.
            filters: Optional SearchFilter with buyer tier/identity context.

        Returns:
            List of matching PackageSummary objects.

        Raises:
            MediaKitError: On network or HTTP errors.
        """
        base = self._normalize_url(seller_url)
        headers = self._build_headers()

        body: dict = {"query": query}
        if filters:
            body["buyer_tier"] = filters.buyer_tier
            if filters.agency_id:
                body["agency_id"] = filters.agency_id
            if filters.advertiser_id:
                body["advertiser_id"] = filters.advertiser_id

        try:
            resp = await self._http.post(f"{base}/media-kit/search", headers=headers, json=body)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise MediaKitError(
                message=f"Failed to connect to {seller_url}: {exc}",
                seller_url=seller_url,
            ) from exc

        data = await self._handle_response(resp, seller_url)
        return [self._parse_package_summary(p, seller_url) for p in data.get("results", [])]

    async def aggregate_across_sellers(
        self,
        seller_urls: list[str],
    ) -> list[PackageSummary]:
        """Query multiple sellers in parallel and aggregate their packages.

        Calls list_packages on each seller concurrently. Sellers that fail
        (network errors, timeouts) are silently skipped.

        Args:
            seller_urls: List of seller base URLs.

        Returns:
            Combined list of PackageSummary from all reachable sellers.
        """
        if not seller_urls:
            return []

        async def _fetch_one(url: str) -> list[PackageSummary]:
            try:
                return await self.list_packages(url)
            except MediaKitError:
                logger.warning("Failed to fetch packages from %s, skipping", url)
                return []

        results = await asyncio.gather(*[_fetch_one(url) for url in seller_urls])
        # Flatten
        return [pkg for seller_pkgs in results for pkg in seller_pkgs]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> "MediaKitClient":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
