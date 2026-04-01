# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook signature verification utilities.

Provides HMAC-SHA256 signature verification for incoming webhooks from sellers.
"""

import hashlib
import hmac
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: dict[str, Any], signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from webhook.

    Args:
        payload: Webhook payload dictionary
        signature: Signature from X-Webhook-Signature header (format: "sha256=<hex>")
        secret: Shared secret with seller

    Returns:
        True if signature is valid, False otherwise

    Example:
        >>> payload = {"event_type": "deal.created", "deal_id": "DEMO-123"}
        >>> signature = "sha256=abc123..."
        >>> secret = "webhook_secret_xyz"
        >>> verify_webhook_signature(payload, signature, secret)
        True
    """
    if not signature or not secret:
        logger.warning("Missing signature or secret for webhook verification")
        return False

    try:
        # Generate expected signature
        message = json.dumps(payload, sort_keys=True).encode()
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        # Extract hex from "sha256=<hex>" format
        if signature.startswith("sha256="):
            sig_hex = signature[7:]
        else:
            sig_hex = signature

        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected, sig_hex)

    except Exception as e:
        logger.error("Webhook signature verification failed: %s", e)
        return False


def generate_signature(payload: dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for testing.

    Args:
        payload: Payload dictionary
        secret: Secret key

    Returns:
        Signature in format "sha256=<hex>"
    """
    message = json.dumps(payload, sort_keys=True).encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"sha256={signature}"
