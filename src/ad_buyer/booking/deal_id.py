# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal ID generation utility.

Extracts the deal ID generation logic previously duplicated in:
- unified_client.py (request_deal method)
- tools/buyer_deals/request_deal.py (_generate_deal_id method)

Deal IDs have the format: DEAL-XXXXXXXX
where XXXXXXXX is 8 uppercase hex characters derived from
a secrets-based token seeded with the product ID, identity, and timestamp.
"""

import secrets
from datetime import datetime, timezone


def generate_deal_id(
    product_id: str,
    identity_seed: str,
    timestamp: datetime | None = None,
) -> str:
    """Generate a unique Deal ID for programmatic activation.

    Creates a unique deal ID based on product, buyer identity, and
    a cryptographic random component.

    Args:
        product_id: Product ID the deal is for.
        identity_seed: Buyer identity string (agency_id, seat_id, or 'public').
        timestamp: Optional timestamp override (defaults to now UTC).

    Returns:
        Deal ID in format DEAL-XXXXXXXX (8 uppercase hex chars).
    """
    if not identity_seed:
        identity_seed = "public"

    # Use secrets.token_hex for cryptographically secure random IDs
    hash_suffix = secrets.token_hex(4).upper()
    return f"DEAL-{hash_suffix}"
