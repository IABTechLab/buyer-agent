# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Authentication helpers for the Ad Buyer Agent.

Outbound (seller credentials):
    :class:`ApiKeyStore`, :class:`AuthMiddleware`

Inbound (operator control plane):
    :func:`require_operator_key`, :class:`OperatorKeyService`
"""

from .dependencies import require_operator_key
from .factory import get_operator_key_service, reset_operator_key_service
from .key_store import ApiKeyStore
from .middleware import AuthMiddleware, AuthResponse
from .operator_key_service import OperatorKeyService

__all__ = [
    "ApiKeyStore",
    "AuthMiddleware",
    "AuthResponse",
    "OperatorKeyService",
    "get_operator_key_service",
    "require_operator_key",
    "reset_operator_key_service",
]
