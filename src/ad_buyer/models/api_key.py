# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""API key models for inbound operator authentication.

Operator keys protect the buyer-agent control plane (REST + MCP over HTTP).
The plaintext key is returned exactly once at creation time and never stored;
only a SHA-256 hash is persisted.

Key format: abk_live_{token} (ad-buyer-key)
"""

import hashlib
import secrets
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

API_KEY_PREFIX = "abk_live_"


class ApiKeyRole(str, Enum):
    """Role attached to an inbound API key.

    Buyer-agent only mints OPERATOR keys for its control plane.
    A BUYER value exists for forward-compat / safe deserialization defaults
    and is never issued by this service.
    """

    BUYER = "buyer"
    OPERATOR = "operator"


def generate_api_key() -> str:
    """Generate a new API key with prefix.

    Returns the full key (shown once to the operator).
    256 bits of entropy via secrets.token_urlsafe(32).
    """
    token = secrets.token_urlsafe(32)
    return f"{API_KEY_PREFIX}{token}"


def hash_api_key(full_key: str) -> str:
    """SHA-256 hash of the full API key for storage lookup."""
    return hashlib.sha256(full_key.encode()).hexdigest()


class ApiKeyRecord(BaseModel):
    """Stored record for an issued API key.

    The plaintext key is never stored — only the hash.
    """

    key_id: str
    key_hash: str
    key_prefix_hint: str
    role: ApiKeyRole = ApiKeyRole.OPERATOR
    label: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    revoked: bool = False
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    use_count: int = 0

    @property
    def is_expired(self) -> bool:
        """Whether the key has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_active(self) -> bool:
        """Whether the key is valid for authentication."""
        return not self.revoked and not self.is_expired


class OperatorApiKeyCreateRequest(BaseModel):
    """Request to create an OPERATOR-role API key."""

    label: str = ""
    expires_in_days: Optional[int] = None


class ApiKeyCreateResponse(BaseModel):
    """Response after creating an API key.

    The ``api_key`` field contains the full key and is shown
    ONLY in this response. It cannot be retrieved again.
    """

    key_id: str
    api_key: str
    role: ApiKeyRole = ApiKeyRole.OPERATOR
    label: str
    expires_at: Optional[datetime] = None
    warning: str = "Store this key securely. It will not be shown again."


class ApiKeyInfo(BaseModel):
    """Public info about an API key (no secret material)."""

    key_id: str
    key_prefix_hint: str
    role: ApiKeyRole = ApiKeyRole.OPERATOR
    label: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked: bool = False
    is_active: bool = True
    last_used_at: Optional[datetime] = None
    use_count: int = 0
