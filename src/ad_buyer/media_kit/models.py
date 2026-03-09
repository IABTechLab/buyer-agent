# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-side models for consuming seller media kits.

These mirror the seller's public/authenticated package views but are owned
by the buyer system. They represent what the buyer receives and works with
when browsing seller inventory.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PackageSummary:
    """Summary view of a seller package (public or authenticated).

    Corresponds to the seller's PublicPackageView. Contains enough info
    for browsing and filtering without full placement details.
    """

    package_id: str
    name: str
    description: Optional[str] = None
    ad_formats: list[str] = field(default_factory=list)
    device_types: list[int] = field(default_factory=list)
    cat: list[str] = field(default_factory=list)
    cattax: int = 2
    geo_targets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    price_range: str = ""  # e.g. "$28-$42 CPM" (public view)
    rate_type: str = "cpm"
    is_featured: bool = False
    # Set when aggregating across sellers
    seller_url: Optional[str] = None


@dataclass
class PlacementDetail:
    """Details of a product placement within a package."""

    product_id: str
    product_name: str
    ad_formats: list[str] = field(default_factory=list)
    device_types: list[int] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class PackageDetail(PackageSummary):
    """Detailed view of a seller package (authenticated).

    Extends PackageSummary with exact pricing, placements, audience segments,
    and negotiation info. Only available with API key auth.
    """

    exact_price: Optional[float] = None
    floor_price: Optional[float] = None
    currency: str = "USD"
    placements: list[PlacementDetail] = field(default_factory=list)
    audience_segment_ids: list[str] = field(default_factory=list)
    negotiation_enabled: bool = False
    volume_discounts_available: bool = False


@dataclass
class MediaKit:
    """Overview of a seller's media kit.

    Returned by GET /media-kit, contains the seller name, total package count,
    featured packages, and the full package list.
    """

    seller_url: str
    seller_name: str = ""
    total_packages: int = 0
    featured: list[PackageSummary] = field(default_factory=list)
    all_packages: list[PackageSummary] = field(default_factory=list)


@dataclass
class SearchFilter:
    """Filters for searching seller media kits.

    Maps to the seller's MediaKitSearchRequest body fields.
    """

    query: str = ""
    buyer_tier: str = "public"
    agency_id: Optional[str] = None
    advertiser_id: Optional[str] = None


class MediaKitError(Exception):
    """Error from media kit operations."""

    def __init__(self, message: str, seller_url: str = "", status_code: int = 0):
        super().__init__(message)
        self.seller_url = seller_url
        self.status_code = status_code
