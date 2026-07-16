# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Agent registry discovery client for finding seller agents via IAB AAMP."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .aamp_client import AampRegistryClient
from .cache import SellerCache
from .client import RegistryClient
from .models import AgentCapability, AgentCard, AgentTrustInfo, TrustLevel

if TYPE_CHECKING:
    from ..config.settings import Settings


def create_registry_client(settings: Settings) -> AampRegistryClient | RegistryClient:
    """Build the discovery client from settings — config-swap, not code-swap.

    When ``AAMP_REGISTRY_URL`` is configured, discovery goes through the
    real IAB agent registry (``/api/agents``, JWT bearer auth) via the
    shared contract library. Otherwise the legacy in-process/sandbox path
    (``IAB_SERVER_URL``) is used — the default for tests and local dev.
    """
    if settings.aamp_registry_url:
        return AampRegistryClient(
            base_url=settings.aamp_registry_url,
            auth_token=settings.aamp_registry_auth_token or None,
        )
    return RegistryClient(registry_url=settings.iab_server_url)


__all__ = [
    "AampRegistryClient",
    "AgentCapability",
    "AgentCard",
    "AgentTrustInfo",
    "RegistryClient",
    "SellerCache",
    "TrustLevel",
    "create_registry_client",
]
