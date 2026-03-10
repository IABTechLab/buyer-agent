# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: auth -> session -> negotiation coordination.

Tests the interaction between the auth middleware, session manager,
and negotiation client modules. Verifies that authentication flows
through to session creation and negotiation execution.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware, AuthResponse
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.models import (
    NegotiationOutcome,
    NegotiationResult,
    NegotiationRound,
    NegotiationSession,
)
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.sessions.session_manager import SessionManager
from ad_buyer.sessions.session_store import SessionRecord, SessionStore


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


class TestSessionManagerIntegration:
    """Tests session manager with mock HTTP endpoints."""

    @pytest.mark.asyncio
    async def test_create_session_stores_record(
        self, tmp_session_store_path: str
    ):
        """create_session should persist the session record to the store."""
        manager = SessionManager(store_path=tmp_session_store_path)
        seller_url = "http://seller.example.com"

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "sess-abc-123",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            session_id = await manager.create_session(
                seller_url,
                buyer_identity={"seat_id": "ttd-123"},
            )

        assert session_id == "sess-abc-123"
        # Verify it's in the store
        record = manager.store.get(seller_url)
        assert record is not None
        assert record.session_id == "sess-abc-123"

    @pytest.mark.asyncio
    async def test_get_or_create_reuses_active_session(
        self, tmp_session_store_path: str
    ):
        """get_or_create_session should reuse an active session from the store."""
        manager = SessionManager(store_path=tmp_session_store_path)
        seller_url = "http://seller.example.com"

        # Manually insert an active session
        record = SessionRecord(
            session_id="existing-sess-001",
            seller_url=seller_url,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        # Should return existing session without HTTP call
        session_id = await manager.get_or_create_session(seller_url)
        assert session_id == "existing-sess-001"

    @pytest.mark.asyncio
    async def test_session_expiry_triggers_recreation(
        self, tmp_session_store_path: str
    ):
        """Expired session should trigger creation of a new one."""
        manager = SessionManager(store_path=tmp_session_store_path)
        seller_url = "http://seller.example.com"

        # Insert an expired session
        record = SessionRecord(
            session_id="expired-sess",
            seller_url=seller_url,
            created_at=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        )
        manager.store.save(record)

        # Mock HTTP for new session creation
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "new-sess-002",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            session_id = await manager.get_or_create_session(seller_url)

        assert session_id == "new-sess-002"

    @pytest.mark.asyncio
    async def test_send_message_with_session_renewal(
        self, tmp_session_store_path: str
    ):
        """send_message should auto-renew when seller returns 404 (expired)."""
        manager = SessionManager(store_path=tmp_session_store_path)
        seller_url = "http://seller.example.com"

        # Insert a session that the seller considers expired
        record = SessionRecord(
            session_id="stale-sess",
            seller_url=seller_url,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        manager.store.save(record)

        # Mock: first message call returns 404, session creation succeeds, retry succeeds
        expired_response = MagicMock()
        expired_response.status_code = 404

        new_session_response = MagicMock()
        new_session_response.status_code = 201
        new_session_response.json.return_value = {
            "session_id": "renewed-sess",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"reply": "Got your message"}

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            # First post -> 404 (expired), second post -> new session, third post -> message
            mock_client.post = AsyncMock(
                side_effect=[expired_response, new_session_response, success_response]
            )
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await manager.send_message(
                seller_url,
                "stale-sess",
                {"type": "query", "content": "list products"},
            )

        assert result["reply"] == "Got your message"
        # Verify the new session was stored
        stored = manager.store.get(seller_url)
        assert stored is not None
        assert stored.session_id == "renewed-sess"


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


class TestAuthSessionNegotiationChain:
    """End-to-end: auth -> session -> negotiation chain."""

    @pytest.mark.asyncio
    async def test_full_auth_session_negotiation_chain(
        self,
        tmp_key_store: ApiKeyStore,
        tmp_session_store_path: str,
    ):
        """Full chain: store API key, create session, run negotiation."""
        seller_url = "http://seller.example.com"

        # Step 1: Store API key for the seller
        tmp_key_store.add_key(seller_url, "seller-api-key-123")

        # Step 2: Verify auth middleware decorates requests
        middleware = AuthMiddleware(key_store=tmp_key_store)
        request = httpx.Request("POST", f"{seller_url}/proposals/p1/counter")
        authed = middleware.add_auth(request)
        assert authed.headers.get("X-Api-Key") == "seller-api-key-123"

        # Step 3: Create a session with the seller
        manager = SessionManager(store_path=tmp_session_store_path)

        session_create_resp = MagicMock()
        session_create_resp.status_code = 201
        session_create_resp.json.return_value = {
            "session_id": "sess-chain-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=session_create_resp)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            session_id = await manager.create_session(
                seller_url, buyer_identity={"seat_id": "ttd-123"}
            )

        assert session_id == "sess-chain-001"

        # Step 4: Run a negotiation with the seller
        strategy = SimpleThresholdStrategy(
            target_cpm=22.0,
            max_cpm=25.0,
            concession_step=1.0,
            max_rounds=3,
        )

        neg_client = NegotiationClient(api_key="seller-api-key-123")

        # Seller accepts on first round at $24
        round1_resp = MagicMock()
        round1_resp.status_code = 200
        round1_resp.json.return_value = {
            "round_number": 1,
            "seller_price": 24.0,
            "action": "counter",
        }
        round1_resp.raise_for_status = MagicMock()

        accept_resp = MagicMock()
        accept_resp.status_code = 200
        accept_resp.json.return_value = {"action": "accepted", "final_price": 24.0}
        accept_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_http = AsyncMock()
            mock_http.post = AsyncMock(side_effect=[round1_resp, accept_resp])
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await neg_client.auto_negotiate(
                seller_url=seller_url,
                proposal_id="prop-chain-001",
                strategy=strategy,
            )

        assert result.outcome == NegotiationOutcome.ACCEPTED
        assert result.final_price == 24.0
