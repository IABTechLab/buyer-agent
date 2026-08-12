# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FastAPI authentication dependencies for inbound operator keys.

Bootstrap: the first operator key is minted out-of-band with
``ad-buyer create-operator-key`` (writes directly to storage — no
network surface). Subsequent keys use ``POST /auth/api-keys/operator``.

Deprecated migration shim: when ``settings.api_key`` is set and no
hashed operator keys exist yet, that plaintext value is accepted as a
single synthetic operator credential (compare only; never minted via HTTP).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

from fastapi import Header, HTTPException

from ..models.api_key import ApiKeyRecord, ApiKeyRole
from .factory import get_operator_key_service

logger = logging.getLogger(__name__)

# Synthetic key_id used when authenticating via deprecated settings.api_key
_LEGACY_API_KEY_ID = "key-legacy-env"


def _extract_key_from_headers(
    authorization: Optional[str] = None,
    x_api_key: Optional[str] = None,
) -> Optional[str]:
    """Extract API key from ``X-Api-Key`` or ``Authorization: Bearer``."""
    if x_api_key:
        return x_api_key
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


def _legacy_operator_record(api_key: str) -> ApiKeyRecord:
    """Build a synthetic operator record for the deprecated API_KEY shim."""
    return ApiKeyRecord(
        key_id=_LEGACY_API_KEY_ID,
        key_hash="legacy",
        key_prefix_hint=(api_key[:12] + "...") if len(api_key) > 12 else "****",
        role=ApiKeyRole.OPERATOR,
        label="legacy-env-api-key",
        created_at=datetime.utcnow(),
    )


def validate_operator_credential(raw_key: str) -> ApiKeyRecord:
    """Validate a raw key string as an operator credential.

    Raises:
        ValueError: with a message suitable for HTTP 401 detail.
        PermissionError: when the key is valid but not operator-role (403).
    """
    from ..config.settings import get_settings

    service = get_operator_key_service()
    settings = get_settings()
    has_db_keys = service.has_active_operator_keys()

    try:
        record = service.validate_key(raw_key)
    except ValueError:
        raise

    if record is not None:
        if record.role != ApiKeyRole.OPERATOR:
            raise PermissionError("Operator credential required")
        return record

    if (
        not has_db_keys
        and settings.api_key
        and secrets.compare_digest(raw_key, settings.api_key)
    ):
        return _legacy_operator_record(settings.api_key)

    raise ValueError("Invalid API key")


async def require_operator_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
) -> ApiKeyRecord:
    """Validate API key and require an OPERATOR-role credential.

    Anonymous → 401. Invalid/revoked/expired → 401. Non-operator role → 403.
    """
    raw_key = _extract_key_from_headers(authorization, x_api_key)
    if raw_key is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return validate_operator_credential(raw_key)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
