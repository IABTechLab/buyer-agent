# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests: error propagation across module boundaries.

Tests that errors in one module correctly propagate to callers without
being swallowed silently, and that partial failures are handled
gracefully by downstream modules.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware
from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.clients.unified_client import Protocol, UnifiedClient, UnifiedResult
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.media_kit.client import MediaKitClient
from ad_buyer.media_kit.models import MediaKitError, PackageSummary
from ad_buyer.models.buyer_identity import BuyerContext, BuyerIdentity, DealType
from ad_buyer.models.flow_state import (
    BookingState,
    ChannelAllocation,
    ExecutionStatus,
)
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.registry.client import RegistryClient
from ad_buyer.sessions.session_manager import SessionManager
from ad_buyer.sessions.session_store import SessionRecord


class TestClientErrorPropagation:
    """Tests error propagation from client layers."""

    @pytest.mark.asyncio
    async def test_opendirect_http_error_propagates(self):
        """HTTP errors from OpenDirectClient should raise to callers."""
        client = OpenDirectClient(base_url="http://fake.test")

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response
        )

        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            with pytest.raises(httpx.HTTPStatusError):
                await client.list_products()

    @pytest.mark.asyncio
    async def test_unified_client_error_in_result(self):
        """UnifiedClient should return error in UnifiedResult, not raise."""
        client = UnifiedClient(base_url="http://fake.test")

        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(
                success=False,
                data=None,
                error="Product not found",
                raw=None,
            )
        )
        client._mcp_client = mock_mcp

        result = await client.get_product("nonexistent")
        assert result.success is False
        assert "not found" in result.error.lower()

        await client.close()

    @pytest.mark.asyncio
    async def test_unified_client_deal_request_with_missing_product(self):
        """request_deal should handle product not found gracefully."""
        client = UnifiedClient(base_url="http://fake.test")

        mock_mcp = AsyncMock()
        mock_mcp.call_tool = AsyncMock(
            return_value=MagicMock(
                success=True,
                data=None,  # No product data
                error="",
                raw=None,
            )
        )
        client._mcp_client = mock_mcp

        result = await client.request_deal(product_id="nonexistent")
        assert result.success is False
        assert "not found" in result.error.lower()

        await client.close()


class TestFlowErrorPropagation:
    """Tests error handling in the DealBookingFlow pipeline."""

    def test_crew_exception_captured_in_flow_errors(
        self, sample_campaign_brief: dict[str, Any]
    ):
        """Exception in portfolio crew should be captured in flow state errors."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        flow.state.campaign_brief = sample_campaign_brief

        with patch(
            "ad_buyer.flows.deal_booking_flow.create_portfolio_crew",
            side_effect=RuntimeError("Crew initialization failed"),
        ):
            brief_result = flow.receive_campaign_brief()
            audience_result = flow.plan_audience(brief_result)
            alloc_result = flow.allocate_budget(audience_result)

        assert alloc_result["status"] == "failed"
        assert flow.state.execution_status == ExecutionStatus.FAILED
        assert any("Budget allocation failed" in e for e in flow.state.errors)

    def test_channel_research_failure_isolated(
        self, sample_campaign_brief: dict[str, Any]
    ):
        """Failure in one channel research should not prevent others."""
        client = OpenDirectClient(base_url="http://fake.test")
        flow = DealBookingFlow(client)
        flow.state.campaign_brief = sample_campaign_brief

        flow.state.budget_allocations["branding"] = ChannelAllocation(
            channel="branding", budget=40000, percentage=40, rationale="Display"
        )
        flow.state.budget_allocations["ctv"] = ChannelAllocation(
            channel="ctv", budget=25000, percentage=25, rationale="CTV"
        )

        # Mock branding crew to succeed
        mock_branding_crew = MagicMock()
        mock_branding_crew.kickoff.return_value = "[]"  # Empty recommendations

        # Mock CTV crew to fail
        mock_ctv_crew = MagicMock()
        mock_ctv_crew.kickoff.side_effect = RuntimeError("CTV crew crashed")

        with patch(
            "ad_buyer.flows.deal_booking_flow.create_branding_crew",
            return_value=mock_branding_crew,
        ), patch(
            "ad_buyer.flows.deal_booking_flow.create_ctv_crew",
            return_value=mock_ctv_crew,
        ):
            alloc_result = {"status": "success"}

            branding_result = flow.research_branding(alloc_result)
            ctv_result = flow.research_ctv(alloc_result)

        # Branding should succeed
        assert branding_result["status"] == "success"
        # CTV should fail but be captured
        assert ctv_result["status"] == "failed"
        assert "CTV research failed" in flow.state.errors[0]


class TestSessionErrorPropagation:
    """Tests error handling in session management."""

    @pytest.mark.asyncio
    async def test_session_creation_failure_raises(
        self, tmp_session_store_path: str
    ):
        """Failed session creation should raise RuntimeError."""
        manager = SessionManager(store_path=tmp_session_store_path)

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service unavailable"

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="Failed to create session"):
                await manager.create_session("http://seller.example.com")

    @pytest.mark.asyncio
    async def test_send_message_failure_after_retry(
        self, tmp_session_store_path: str
    ):
        """send_message should raise after retry also fails."""
        manager = SessionManager(store_path=tmp_session_store_path)
        seller_url = "http://seller.example.com"

        # Insert an active session
        record = SessionRecord(
            session_id="active-sess",
            seller_url=seller_url,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        # Mock: first message -> 404, session creation -> 201, retry message -> 500
        expired_resp = MagicMock()
        expired_resp.status_code = 404

        new_session_resp = MagicMock()
        new_session_resp.status_code = 201
        new_session_resp.json.return_value = {
            "session_id": "new-sess",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        failed_retry_resp = MagicMock()
        failed_retry_resp.status_code = 500

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                side_effect=[expired_resp, new_session_resp, failed_retry_resp]
            )
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="Failed to send message"):
                await manager.send_message(
                    seller_url, "active-sess", {"type": "query"}
                )


class TestNegotiationErrorPropagation:
    """Tests error handling in negotiation flows."""

    @pytest.mark.asyncio
    async def test_negotiation_http_error_propagates(self):
        """HTTP errors during negotiation should raise."""
        strategy = SimpleThresholdStrategy(
            target_cpm=20.0, max_cpm=30.0, concession_step=2.0, max_rounds=3
        )
        client = NegotiationClient()

        with patch("httpx.AsyncClient") as MockAsyncClient:
            mock_http = AsyncMock()
            error_response = MagicMock()
            error_response.status_code = 500
            error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=MagicMock(),
                response=error_response,
            )
            mock_http.post = AsyncMock(return_value=error_response)
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(httpx.HTTPStatusError):
                await client.auto_negotiate(
                    seller_url="http://seller.example.com",
                    proposal_id="prop-err",
                    strategy=strategy,
                )


class TestMediaKitErrorPropagation:
    """Tests error handling in media kit client."""

    @pytest.mark.asyncio
    async def test_media_kit_connection_error_raises(self):
        """Connection error should raise MediaKitError."""
        async with MediaKitClient() as client:
            with patch.object(
                client._http,
                "get",
                side_effect=httpx.ConnectError("Connection refused"),
            ):
                with pytest.raises(MediaKitError) as exc_info:
                    await client.get_media_kit("http://unreachable-seller.test")

            assert "Failed to connect" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_media_kit_http_error_raises(self):
        """HTTP errors (4xx/5xx) should raise MediaKitError with status code."""
        async with MediaKitClient() as client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.json.return_value = {}

            with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = mock_response
                with pytest.raises(MediaKitError) as exc_info:
                    await client.get_media_kit("http://seller.test")

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_aggregate_across_sellers_tolerates_failures(self):
        """aggregate_across_sellers should skip failing sellers silently."""
        async with MediaKitClient() as client:
            # Mock: first seller fails, second succeeds
            good_response = MagicMock()
            good_response.status_code = 200
            good_response.json.return_value = {
                "packages": [
                    {
                        "package_id": "pkg1",
                        "name": "News Package",
                        "ad_formats": ["banner"],
                        "device_types": ["desktop"],
                    }
                ]
            }

            fail_response = MagicMock()
            fail_response.status_code = 500
            fail_response.json.return_value = {}

            call_count = 0

            async def mock_get(url, **kwargs):
                nonlocal call_count
                call_count += 1
                if "bad-seller" in url:
                    return fail_response
                return good_response

            with patch.object(client._http, "get", side_effect=mock_get):
                results = await client.aggregate_across_sellers([
                    "http://bad-seller.test",
                    "http://good-seller.test",
                ])

            # Only packages from the good seller
            assert len(results) == 1
            assert results[0].name == "News Package"


class TestKeyStorePersistenceErrors:
    """Tests key store behavior with filesystem edge cases."""

    def test_corrupted_key_store_file_loads_empty(self, tmp_path):
        """Corrupted JSON file should result in empty key store, not crash."""
        store_path = tmp_path / "corrupted_keys.json"
        store_path.write_text("not valid json {{{", encoding="utf-8")

        store = ApiKeyStore(store_path=store_path)
        assert store.list_sellers() == []
        # Should still be functional after loading
        store.add_key("http://seller.test", "key-1")
        assert store.get_key("http://seller.test") == "key-1"

    def test_missing_store_file_starts_empty(self, tmp_path):
        """Non-existent store file should start with empty keys."""
        store_path = tmp_path / "nonexistent" / "keys.json"
        store = ApiKeyStore(store_path=store_path)
        assert store.list_sellers() == []

        # Should create the file on first write
        store.add_key("http://seller.test", "new-key")
        assert store_path.exists()
