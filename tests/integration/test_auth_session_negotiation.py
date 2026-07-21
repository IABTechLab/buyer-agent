# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: auth -> negotiation coordination.

Tests the interaction between the auth middleware and negotiation client
modules. Verifies that authentication flows through to negotiation
execution.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.models import (
    NegotiationOutcome,
)
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy


class TestAuthToSessionFlow:
    """Tests auth middleware feeding into session manager."""

    def test_key_store_to_middleware_pipeline(self, tmp_key_store: ApiKeyStore):
        """Key stored in ApiKeyStore should be attached by AuthMiddleware."""
        seller_url = "http://seller.example.com"
        tmp_key_store.add_key(seller_url, "seller-secret-123")

        middleware = AuthMiddleware(key_store=tmp_key_store, header_type="api_key")

        # Create a request to the seller
        request = httpx.Request("GET", f"{seller_url}/products")
        authed_request = middleware.add_auth(request)

        assert authed_request.headers.get("X-Api-Key") == "seller-secret-123"

    def test_bearer_auth_mode(self, tmp_key_store: ApiKeyStore):
        """Bearer token mode should use Authorization header."""
        seller_url = "http://seller.example.com"
        tmp_key_store.add_key(seller_url, "bearer-token-xyz")

        middleware = AuthMiddleware(key_store=tmp_key_store, header_type="bearer")
        request = httpx.Request("GET", f"{seller_url}/products")
        authed_request = middleware.add_auth(request)

        assert authed_request.headers.get("Authorization") == "Bearer bearer-token-xyz"

    def test_no_key_stored_leaves_request_unchanged(self, tmp_key_store: ApiKeyStore):
        """If no key is stored for the seller, request should pass through unchanged."""
        middleware = AuthMiddleware(key_store=tmp_key_store)
        request = httpx.Request("GET", "http://unknown-seller.example.com/products")
        authed_request = middleware.add_auth(request)

        assert "X-Api-Key" not in authed_request.headers

    def test_401_response_triggers_reauth(self, tmp_key_store: ApiKeyStore):
        """401 response should signal need for re-authentication."""
        middleware = AuthMiddleware(key_store=tmp_key_store)

        request = httpx.Request("GET", "http://seller.example.com/products")
        response = httpx.Response(401, request=request)
        auth_response = middleware.handle_response(response)

        assert auth_response.needs_reauth is True
        assert auth_response.seller_url == "http://seller.example.com"

    def test_200_response_no_reauth(self, tmp_key_store: ApiKeyStore):
        """200 response should not signal re-authentication."""
        middleware = AuthMiddleware(key_store=tmp_key_store)

        request = httpx.Request("GET", "http://seller.example.com/products")
        response = httpx.Response(200, request=request)
        auth_response = middleware.handle_response(response)

        assert auth_response.needs_reauth is False

    def test_key_rotation(self, tmp_key_store: ApiKeyStore):
        """Rotating a key should update what the middleware attaches."""
        seller_url = "http://seller.example.com"
        tmp_key_store.add_key(seller_url, "old-key")

        middleware = AuthMiddleware(key_store=tmp_key_store)

        # Verify old key
        request = httpx.Request("GET", f"{seller_url}/test")
        authed = middleware.add_auth(request)
        assert authed.headers.get("X-Api-Key") == "old-key"

        # Rotate
        tmp_key_store.rotate_key(seller_url, "new-key")

        authed2 = middleware.add_auth(request)
        assert authed2.headers.get("X-Api-Key") == "new-key"


class TestNegotiationFlowIntegration:
    """Tests negotiation client with strategy and mock seller."""

    @pytest.mark.asyncio
    async def test_auto_negotiate_accept(self):
        """Auto-negotiation where seller price drops below max_cpm."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0,
            max_cpm=28.0,
            concession_step=2.0,
            max_rounds=5,
        )
        client = NegotiationClient()

        # Mock responses: round 1 seller at $30, round 2 seller at $27
        round1_response = MagicMock()
        round1_response.status_code = 200
        round1_response.json.return_value = {
            "round_number": 1,
            "seller_price": 30.0,
            "action": "counter",
            "rationale": "Our standard rate",
        }
        round1_response.raise_for_status = MagicMock()

        round2_response = MagicMock()
        round2_response.status_code = 200
        round2_response.json.return_value = {
            "round_number": 2,
            "seller_price": 27.0,
            "action": "counter",
            "rationale": "Reduced for volume",
        }
        round2_response.raise_for_status = MagicMock()

        accept_response = MagicMock()
        accept_response.status_code = 200
        accept_response.json.return_value = {
            "action": "accepted",
            "final_price": 27.0,
        }
        accept_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(
                side_effect=[round1_response, round2_response, accept_response]
            )
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.auto_negotiate(
                seller_url="http://seller.example.com",
                proposal_id="prop-001",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 27.0
        assert result.rounds_count >= 2

    @pytest.mark.asyncio
    async def test_auto_negotiate_walk_away_max_rounds(self):
        """Auto-negotiation should walk away when max_rounds exceeded."""
        strategy = SimpleThresholdStrategy(
            target_cpm=15.0,
            max_cpm=20.0,
            concession_step=1.0,
            max_rounds=2,
        )
        client = NegotiationClient()

        # Seller stays high every round
        counter_response = MagicMock()
        counter_response.status_code = 200
        counter_response.json.return_value = {
            "round_number": 1,
            "seller_price": 35.0,
            "action": "counter",
        }
        counter_response.raise_for_status = MagicMock()

        counter_response_2 = MagicMock()
        counter_response_2.status_code = 200
        counter_response_2.json.return_value = {
            "round_number": 2,
            "seller_price": 34.0,
            "action": "counter",
        }
        counter_response_2.raise_for_status = MagicMock()

        # Walk away response (decline)
        decline_response = MagicMock()
        decline_response.status_code = 200
        decline_response.json.return_value = {"action": "declined"}
        decline_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(
                side_effect=[counter_response, counter_response_2, decline_response]
            )
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.auto_negotiate(
                seller_url="http://seller.example.com",
                proposal_id="prop-002",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.WALKED_AWAY
        assert result.final_price is None
