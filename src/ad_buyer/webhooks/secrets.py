# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook secret management.

Manages webhook secrets per seller for signature verification.
Supports both in-memory storage and environment variable configuration.
"""

import logging
import uuid
from datetime import datetime

from ..models.webhooks import WebhookSecretConfig

logger = logging.getLogger(__name__)


class WebhookSecretStore:
    """Manage webhook secrets per seller.

    Stores secrets in memory. For production, consider backing this
    with a database or secrets management service.
    """

    def __init__(self) -> None:
        self._secrets: dict[str, WebhookSecretConfig] = {}

    def set_secret(self, seller_url: str, secret: str) -> None:
        """Store webhook secret for a seller.

        Args:
            seller_url: Seller base URL (e.g., "http://localhost:8001")
            secret: Webhook secret key
        """
        if seller_url in self._secrets:
            config = self._secrets[seller_url]
            config.secret = secret
            config.updated_at = datetime.utcnow()
        else:
            self._secrets[seller_url] = WebhookSecretConfig(
                seller_url=seller_url,
                secret=secret,
            )

        logger.info("Webhook secret set for seller: %s", seller_url)

    def get_secret(self, seller_url: str) -> str | None:
        """Retrieve webhook secret for a seller.

        Args:
            seller_url: Seller base URL

        Returns:
            Secret key if found, None otherwise
        """
        config = self._secrets.get(seller_url)
        return config.secret if config else None

    def rotate_secret(self, seller_url: str) -> str:
        """Generate and store new secret for a seller.

        Args:
            seller_url: Seller base URL

        Returns:
            Newly generated secret
        """
        new_secret = uuid.uuid4().hex
        self.set_secret(seller_url, new_secret)
        logger.info("Webhook secret rotated for seller: %s", seller_url)
        return new_secret

    def delete_secret(self, seller_url: str) -> bool:
        """Delete webhook secret for a seller.

        Args:
            seller_url: Seller base URL

        Returns:
            True if deleted, False if not found
        """
        if seller_url in self._secrets:
            del self._secrets[seller_url]
            logger.info("Webhook secret deleted for seller: %s", seller_url)
            return True
        return False

    def list_sellers(self) -> list[str]:
        """List all sellers with configured secrets.

        Returns:
            List of seller URLs
        """
        return list(self._secrets.keys())


# Global singleton instance
_secret_store: WebhookSecretStore | None = None


def get_secret_store() -> WebhookSecretStore:
    """Get or create the global webhook secret store.

    Returns:
        WebhookSecretStore singleton instance
    """
    global _secret_store
    if _secret_store is None:
        _secret_store = WebhookSecretStore()
    return _secret_store
