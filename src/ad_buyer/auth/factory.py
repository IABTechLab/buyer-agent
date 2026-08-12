# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Factory helpers for inbound operator key services."""

from __future__ import annotations

from ..auth.operator_key_service import OperatorKeyService
from ..config.settings import get_settings
from ..storage.api_key_store import OperatorApiKeyStore

_store: OperatorApiKeyStore | None = None
_service: OperatorKeyService | None = None
_store_url: str | None = None


def get_operator_key_service(
    database_url: str | None = None,
    *,
    force_new: bool = False,
) -> OperatorKeyService:
    """Return a connected OperatorKeyService (module singleton).

    Args:
        database_url: Override DB URL (defaults to settings.database_url).
        force_new: When True, disconnect any cached store and create a fresh
            service (used by tests / CLI one-shots that need isolation).
    """
    global _store, _service, _store_url

    url = database_url or get_settings().database_url

    if force_new and _store is not None:
        _store.disconnect()
        _store = None
        _service = None
        _store_url = None

    if _service is not None and _store is not None and _store_url == url:
        return _service

    if _store is not None:
        _store.disconnect()

    store = OperatorApiKeyStore(url)
    store.connect()
    _store = store
    _store_url = url
    _service = OperatorKeyService(store)
    return _service


def reset_operator_key_service() -> None:
    """Disconnect and clear the cached operator key service (tests)."""
    global _store, _service, _store_url
    if _store is not None:
        _store.disconnect()
    _store = None
    _service = None
    _store_url = None
