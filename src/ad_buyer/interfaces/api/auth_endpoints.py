# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Operator API key lifecycle endpoints.

All routes require an existing OPERATOR credential. Bootstrap the FIRST
operator key with ``ad-buyer create-operator-key`` (direct storage write).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...auth.dependencies import require_operator_key
from ...auth.factory import get_operator_key_service
from ...models.api_key import OperatorApiKeyCreateRequest

router = APIRouter(tags=["Authentication"])


class CreateOperatorApiKeyBody(BaseModel):
    """Request body for minting an additional operator key."""

    label: str = Field(default="", description="Human-readable label")
    expires_in_days: Optional[int] = Field(
        default=None,
        description="Days until expiry; omit for never-expires",
    )


@router.post("/auth/api-keys/operator")
async def create_operator_api_key(
    request: CreateOperatorApiKeyBody,
    _operator=Depends(require_operator_key),
):
    """Create a new OPERATOR-role API key.

    Requires an existing operator credential. Bootstrap the FIRST
    operator key with ``ad-buyer create-operator-key``.

    The response contains the full API key which is shown ONLY ONCE.
    """
    service = get_operator_key_service()
    try:
        response = service.create_operator_key(
            OperatorApiKeyCreateRequest(
                label=request.label,
                expires_in_days=request.expires_in_days,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return response.model_dump(mode="json")


@router.get("/auth/api-keys")
async def list_api_keys(
    _operator=Depends(require_operator_key),
):
    """List all API keys (metadata only, no secrets)."""
    service = get_operator_key_service()
    keys = service.list_keys()
    return {"keys": [k.model_dump(mode="json") for k in keys], "count": len(keys)}


@router.get("/auth/api-keys/{key_id}")
async def get_api_key(
    key_id: str,
    _operator=Depends(require_operator_key),
):
    """Get metadata for a single API key."""
    service = get_operator_key_service()
    info = service.get_key_info(key_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"API key {key_id!r} not found")
    return info.model_dump(mode="json")


@router.delete("/auth/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    _operator=Depends(require_operator_key),
):
    """Revoke an API key by key_id."""
    service = get_operator_key_service()
    if not service.revoke_key(key_id):
        raise HTTPException(status_code=404, detail=f"API key {key_id!r} not found")
    info = service.get_key_info(key_id)
    return {
        "revoked": True,
        "key": info.model_dump(mode="json") if info else {"key_id": key_id},
    }
