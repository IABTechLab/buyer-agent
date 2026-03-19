# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal ID generation utility.

Extracts the deal ID generation logic previously duplicated in:
- unified_client.py (request_deal method)
- tools/dsp/request_deal.py (_generate_deal_id method)

Deal IDs have the format: DEAL-XXXXXXXX
where XXXXXXXX is 8 uppercase hex characters derived from
an MD5 hash of the product ID, identity seed, and timestamp.
"""

import hashlib
from datetime import datetime


def generate_deal_id(
    product_id: str,
    identity_seed: str,
    timestamp: datetime | None = None,
) -> str:
    """Generate a unique Deal ID for programmatic activation.

    Creates a semi-random but reproducible deal ID based on
    product, buyer identity, and timestamp.

    Args:
        product_id: Product ID the deal is for.
        identity_seed: Buyer identity string (agency_id, seat_id, or 'public').
        timestamp: Optional timestamp override (defaults to now).

    Returns:
        Deal ID in format DEAL-XXXXXXXX (8 uppercase hex chars).
    """
    if not identity_seed:
        identity_seed = "public"

    if timestamp is None:
        timestamp = datetime.now()

    timestamp_str = timestamp.strftime("%Y%m%d%H%M")
    seed = f"{product_id}-{identity_seed}-{timestamp_str}"
    hash_suffix = hashlib.md5(seed.encode()).hexdigest()[:8].upper()
    return f"DEAL-{hash_suffix}"
