# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seller discovery application service.

Owns the discovery business logic that used to live inline in the MCP
interface layer: registry seller discovery, per-seller media-kit fetch,
and multi-seller comparison.  The interface layer passes in an
already-constructed registry / media-kit client (the same objects tests
patch at the interface seam) and this service performs the query and
shapes the JSON-serialisable result dict.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..media_kit.client import MediaKitClient
from ..media_kit.models import MediaKitError
from ..registry.client import RegistryClient

logger = logging.getLogger(__name__)


def _now() -> str:
    """Current UTC timestamp as ISO 8601 (matches prior interface output)."""
    return datetime.now(UTC).isoformat()


async def discover_sellers(
    registry: RegistryClient,
    capability: str | None = None,
) -> dict[str, Any]:
    """Discover seller agents from the registry, optionally filtered."""
    try:
        caps_filter = [capability] if capability else None
        sellers = await registry.discover_sellers(capabilities_filter=caps_filter)

        seller_list = [
            {
                "agent_id": seller.agent_id,
                "name": seller.name,
                "url": seller.url,
                "capabilities": [
                    {"name": c.name, "description": c.description, "tags": c.tags}
                    for c in seller.capabilities
                ],
                "trust_level": seller.trust_level.value,
                "protocols": seller.protocols,
            }
            for seller in sellers
        ]

        return {
            "total": len(seller_list),
            "sellers": seller_list,
            "timestamp": _now(),
        }
    except Exception as exc:  # noqa: BLE001 - discovery must degrade to an error payload
        logger.warning("Failed to discover sellers: %s", exc)
        return {
            "error": f"Failed to discover sellers: {exc}",
            "total": 0,
            "sellers": [],
            "timestamp": _now(),
        }


async def get_seller_media_kit(
    client: MediaKitClient,
    seller_url: str,
) -> dict[str, Any]:
    """Fetch a single seller's media kit and summarise its packages."""
    try:
        kit = await client.get_media_kit(seller_url)

        packages = [
            {
                "package_id": pkg.package_id,
                "name": pkg.name,
                "description": pkg.description,
                "ad_formats": pkg.ad_formats,
                "device_types": pkg.device_types,
                "price_range": pkg.price_range,
                "rate_type": pkg.rate_type,
                "is_featured": pkg.is_featured,
                "geo_targets": pkg.geo_targets,
                "tags": pkg.tags,
            }
            for pkg in kit.all_packages
        ]

        return {
            "seller_name": kit.seller_name,
            "seller_url": kit.seller_url,
            "total_packages": kit.total_packages,
            "packages": packages,
            "timestamp": _now(),
        }
    except MediaKitError as exc:
        logger.warning("Failed to fetch media kit from %s: %s", seller_url, exc)
        return {
            "error": f"Failed to fetch media kit: {exc}",
            "seller_url": seller_url,
            "timestamp": _now(),
        }
    except Exception as exc:  # noqa: BLE001 - surface unexpected errors as a payload
        logger.warning("Unexpected error fetching media kit from %s: %s", seller_url, exc)
        return {
            "error": f"Unexpected error: {exc}",
            "seller_url": seller_url,
            "timestamp": _now(),
        }


async def compare_sellers(
    client: MediaKitClient,
    seller_urls: list[str],
) -> dict[str, Any]:
    """Fetch and compare media kits across multiple sellers."""
    sellers_data: list[dict[str, Any]] = []
    total_packages = 0
    all_ad_formats: set[str] = set()

    for url in seller_urls:
        try:
            kit = await client.get_media_kit(url)

            seller_formats: set[str] = set()
            packages = []
            for pkg in kit.all_packages:
                seller_formats.update(pkg.ad_formats)
                packages.append(
                    {
                        "package_id": pkg.package_id,
                        "name": pkg.name,
                        "price_range": pkg.price_range,
                        "ad_formats": pkg.ad_formats,
                        "rate_type": pkg.rate_type,
                    }
                )

            all_ad_formats.update(seller_formats)
            total_packages += len(packages)

            sellers_data.append(
                {
                    "seller_url": url,
                    "seller_name": kit.seller_name,
                    "total_packages": len(packages),
                    "ad_formats": sorted(seller_formats),
                    "packages": packages,
                }
            )
        except (MediaKitError, Exception) as exc:  # noqa: BLE001 - per-seller isolation
            logger.warning("Failed to fetch media kit from %s: %s", url, exc)
            sellers_data.append(
                {
                    "seller_url": url,
                    "error": f"Failed to fetch media kit: {exc}",
                    "total_packages": 0,
                    "ad_formats": [],
                    "packages": [],
                }
            )

    return {
        "sellers_compared": len(seller_urls),
        "sellers": sellers_data,
        "summary": {
            "total_packages_across_sellers": total_packages,
            "all_ad_formats": sorted(all_ad_formats),
            "sellers_reachable": sum(1 for s in sellers_data if "error" not in s),
            "sellers_unreachable": sum(1 for s in sellers_data if "error" in s),
        },
        "timestamp": _now(),
    }
