# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Inbound operator API key management for the buyer agent.

Handles creation, lookup, revocation, and listing of OPERATOR-role keys
stored as SHA-256 hashes in SQLite. The first key must be bootstrapped
via ``ad-buyer create-operator-key`` (CLI writes directly to storage).
Subsequent keys may be minted over HTTP with an existing operator credential.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..models.api_key import (
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyRecord,
    ApiKeyRole,
    OperatorApiKeyCreateRequest,
    generate_api_key,
    hash_api_key,
)
from ..storage.api_key_store import OperatorApiKeyStore

logger = logging.getLogger(__name__)


class OperatorKeyService:
    """Manages OPERATOR-role API keys for buyer-agent inbound auth."""

    def __init__(self, store: OperatorApiKeyStore):
        self._store = store

    def create_operator_key(
        self, request: OperatorApiKeyCreateRequest
    ) -> ApiKeyCreateResponse:
        """Issue a new OPERATOR-role API key.

        Rejects if an active (non-revoked, non-expired) operator key
        already uses the same label. Revoked/expired labels may be reused.

        Returns the full key exactly once.

        Raises:
            ValueError: If an active operator key already has this label.
        """
        for existing in self.list_keys():
            if (
                existing.role == ApiKeyRole.OPERATOR
                and existing.is_active
                and existing.label == request.label
            ):
                raise ValueError(
                    f"An active operator key with label {request.label!r} "
                    f"already exists ({existing.key_id})"
                )

        full_key = generate_api_key()
        key_hash = hash_api_key(full_key)
        key_id = f"key-{uuid.uuid4().hex[:8]}"

        expires_at = None
        if request.expires_in_days is not None:
            expires_at = datetime.utcnow() + timedelta(days=request.expires_in_days)

        record = ApiKeyRecord(
            key_id=key_id,
            key_hash=key_hash,
            key_prefix_hint=full_key[:12] + "...",
            role=ApiKeyRole.OPERATOR,
            label=request.label,
            expires_at=expires_at,
        )
        self._store.insert(record)

        logger.info(
            "Operator API key %s created (label: %s)",
            key_id,
            request.label,
        )

        return ApiKeyCreateResponse(
            key_id=key_id,
            api_key=full_key,
            role=ApiKeyRole.OPERATOR,
            label=request.label,
            expires_at=expires_at,
        )

    def validate_key(self, full_key: str) -> Optional[ApiKeyRecord]:
        """Look up and validate an API key.

        Returns:
            ApiKeyRecord if valid, None if not found.

        Raises:
            ValueError: If key is found but revoked or expired.
        """
        key_hash = hash_api_key(full_key)
        record = self._store.get_by_hash(key_hash)
        if record is None:
            return None

        if record.revoked:
            raise ValueError(f"API key {record.key_id} has been revoked")

        if record.is_expired:
            raise ValueError(f"API key {record.key_id} has expired")

        record.last_used_at = datetime.utcnow()
        record.use_count += 1
        self._store.update(record)
        return record

    def get_key_info(self, key_id: str) -> Optional[ApiKeyInfo]:
        """Get info about a key by its key_id (not the secret)."""
        record = self._store.get_by_id(key_id)
        if record is None:
            return None
        return self._to_info(record)

    def list_keys(self) -> list[ApiKeyInfo]:
        """List all API keys (metadata only, no secrets)."""
        return [self._to_info(r) for r in self._store.list_all()]

    def list_operator_keys(self, *, include_inactive: bool = False) -> list[ApiKeyInfo]:
        """List OPERATOR-role API keys (metadata only, no secrets)."""
        keys = [info for info in self.list_keys() if info.role == ApiKeyRole.OPERATOR]
        if not include_inactive:
            keys = [info for info in keys if info.is_active]
        return keys

    def has_active_operator_keys(self) -> bool:
        """True if at least one active (non-revoked, non-expired) operator key exists."""
        return any(info.is_active for info in self.list_operator_keys())

    def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key. Returns True if found and revoked."""
        record = self._store.get_by_id(key_id)
        if record is None:
            return False
        record.revoked = True
        record.revoked_at = datetime.utcnow()
        self._store.update(record)
        logger.info("API key %s revoked", key_id)
        return True

    def delete_operator_key(
        self,
        *,
        key_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> ApiKeyInfo:
        """Revoke an OPERATOR-role API key by key_id or label (bootstrap).

        Soft-revokes so the label can be reused by a subsequent
        ``create_operator_key``.

        Raises:
            ValueError: If neither identifier is given, the key is missing,
                is not an operator key, or is already inactive.
        """
        if not key_id and not label:
            raise ValueError("Provide key_id or label to delete an operator key")

        target: Optional[ApiKeyInfo] = None
        if key_id:
            target = self.get_key_info(key_id)
            if target is None:
                raise ValueError(f"API key {key_id!r} not found")
            if target.role != ApiKeyRole.OPERATOR:
                raise ValueError(
                    f"API key {key_id!r} is a {target.role.value} key, not an "
                    "operator key."
                )
        else:
            matches = [
                info
                for info in self.list_keys()
                if (
                    info.role == ApiKeyRole.OPERATOR
                    and info.is_active
                    and info.label == label
                )
            ]
            if not matches:
                raise ValueError(f"No active operator key with label {label!r} found")
            if len(matches) > 1:
                ids = ", ".join(m.key_id for m in matches)
                raise ValueError(
                    f"Multiple active operator keys share label {label!r} "
                    f"({ids}); pass --key-id to disambiguate"
                )
            target = matches[0]

        if not target.is_active:
            raise ValueError(
                f"Operator key {target.key_id!r} is already inactive "
                f"(revoked or expired)"
            )

        if not self.revoke_key(target.key_id):
            raise ValueError(f"Failed to revoke operator key {target.key_id!r}")

        info = self.get_key_info(target.key_id)
        assert info is not None
        return info

    @staticmethod
    def _to_info(record: ApiKeyRecord) -> ApiKeyInfo:
        return ApiKeyInfo(
            key_id=record.key_id,
            key_prefix_hint=record.key_prefix_hint,
            role=record.role,
            label=record.label,
            created_at=record.created_at,
            expires_at=record.expires_at,
            revoked=record.revoked,
            is_active=record.is_active,
            last_used_at=record.last_used_at,
            use_count=record.use_count,
        )
