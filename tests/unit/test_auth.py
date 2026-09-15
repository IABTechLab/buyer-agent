# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for client-side API key authentication (outbound to sellers)."""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware
from ad_buyer.clients.deals_client import DealsClient
from ad_buyer.orchestration.multi_seller import MultiSellerOrchestrator

# ---------------------------------------------------------------------------
# ApiKeyStore tests
# ---------------------------------------------------------------------------


class TestApiKeyStoreBasics:
    """Test basic key store operations: add, get, remove, list."""

    def test_add_and_get_key(self, tmp_path: Path):
        """Adding a key for a seller URL should be retrievable."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "ask_live_abc123")
        assert store.get_key("https://seller1.example.com") == "ask_live_abc123"

    def test_get_key_nonexistent_returns_none(self, tmp_path: Path):
        """Getting a key for an unknown seller should return None."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        assert store.get_key("https://unknown.example.com") is None

    def test_remove_key(self, tmp_path: Path):
        """Removing a key should make it no longer retrievable."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "ask_live_abc123")
        removed = store.remove_key("https://seller1.example.com")
        assert removed is True
        assert store.get_key("https://seller1.example.com") is None

    def test_remove_nonexistent_key_returns_false(self, tmp_path: Path):
        """Removing a key that doesn't exist should return False."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        assert store.remove_key("https://unknown.example.com") is False

    def test_list_sellers(self, tmp_path: Path):
        """Listing sellers should return all registered seller URLs."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "key1")
        store.add_key("https://seller2.example.com", "key2")
        sellers = store.list_sellers()
        assert set(sellers) == {"https://seller1.example.com", "https://seller2.example.com"}

    def test_list_sellers_empty(self, tmp_path: Path):
        """Listing sellers when store is empty should return empty list."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        assert store.list_sellers() == []

    def test_replace_existing_key(self, tmp_path: Path):
        """Adding a key for an existing seller should replace the old key."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "old_key")
        store.add_key("https://seller1.example.com", "new_key")
        assert store.get_key("https://seller1.example.com") == "new_key"

    def test_url_normalization(self, tmp_path: Path):
        """Trailing slashes should be normalized for consistent lookup."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com/", "key1")
        assert store.get_key("https://seller1.example.com") == "key1"
        assert store.get_key("https://seller1.example.com/") == "key1"


class TestApiKeyStorePersistence:
    """Test that keys persist to disk and can be reloaded."""

    def test_persistence_across_instances(self, tmp_path: Path):
        """Keys should persist to disk and load when a new store is created."""
        store_path = tmp_path / "keys.json"
        store1 = ApiKeyStore(store_path=store_path)
        store1.add_key("https://seller1.example.com", "ask_live_abc123")

        store2 = ApiKeyStore(store_path=store_path)
        assert store2.get_key("https://seller1.example.com") == "ask_live_abc123"

    def test_keys_not_stored_plaintext(self, tmp_path: Path):
        """Raw API keys should not appear as plaintext in the store file."""
        store_path = tmp_path / "keys.json"
        store = ApiKeyStore(store_path=store_path)
        store.add_key("https://seller1.example.com", "ask_live_secret_value")

        raw_content = store_path.read_text()
        # The literal key value should not be directly visible in the file
        assert "ask_live_secret_value" not in raw_content

    def test_corrupted_file_handled_gracefully(self, tmp_path: Path):
        """A corrupted store file should not crash; store starts empty."""
        store_path = tmp_path / "keys.json"
        store_path.write_text("not valid json{{{")
        store = ApiKeyStore(store_path=store_path)
        assert store.list_sellers() == []


class TestApiKeyStoreRotation:
    """Test key rotation (replace + verify old key is gone)."""

    def test_rotate_key(self, tmp_path: Path):
        """Rotating a key should replace the old one."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "old_key")
        store.rotate_key("https://seller1.example.com", "new_key")
        assert store.get_key("https://seller1.example.com") == "new_key"

    def test_rotate_key_nonexistent_adds_it(self, tmp_path: Path):
        """Rotating a key for a new seller should add it."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.rotate_key("https://seller1.example.com", "new_key")
        assert store.get_key("https://seller1.example.com") == "new_key"


# ---------------------------------------------------------------------------
# AuthMiddleware tests
# ---------------------------------------------------------------------------


class TestAuthMiddlewareHeaderAttachment:
    """Test that AuthMiddleware attaches the right headers to requests."""

    def test_attaches_x_api_key_header(self, tmp_path: Path):
        """Middleware should add X-Api-Key header for known sellers."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "ask_live_abc123")
        middleware = AuthMiddleware(key_store=store)

        request = httpx.Request("GET", "https://seller1.example.com/api/products")
        modified = middleware.add_auth(request)
        assert modified.headers.get("X-Api-Key") == "ask_live_abc123"

    def test_attaches_bearer_header_when_configured(self, tmp_path: Path):
        """Middleware should add Authorization: Bearer when header_type is bearer."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "ask_live_abc123")
        middleware = AuthMiddleware(key_store=store, header_type="bearer")

        request = httpx.Request("GET", "https://seller1.example.com/api/products")
        modified = middleware.add_auth(request)
        assert modified.headers.get("Authorization") == "Bearer ask_live_abc123"

    def test_no_header_for_unknown_seller(self, tmp_path: Path):
        """Middleware should not add auth headers for unknown sellers."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        request = httpx.Request("GET", "https://unknown.example.com/api/products")
        modified = middleware.add_auth(request)
        assert "X-Api-Key" not in modified.headers
        assert "Authorization" not in modified.headers

    def test_matches_seller_by_base_url(self, tmp_path: Path):
        """Middleware should match seller by base URL, not full path."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "key1")
        middleware = AuthMiddleware(key_store=store)

        request = httpx.Request("GET", "https://seller1.example.com/deep/nested/path")
        modified = middleware.add_auth(request)
        assert modified.headers.get("X-Api-Key") == "key1"


class TestAuthMiddleware401Handling:
    """Test 401 response handling with retry logic."""

    @pytest.mark.asyncio
    async def test_handle_401_marks_key_invalid(self, tmp_path: Path):
        """A 401 response should mark the key as potentially invalid."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("https://seller1.example.com", "expired_key")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=401,
            request=httpx.Request("GET", "https://seller1.example.com/api/products"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is True
        assert result.seller_url == "https://seller1.example.com"

    @pytest.mark.asyncio
    async def test_handle_200_no_reauth(self, tmp_path: Path):
        """A 200 response should not trigger reauth."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=200,
            request=httpx.Request("GET", "https://seller1.example.com/api/products"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is False

    @pytest.mark.asyncio
    async def test_handle_403_no_reauth(self, tmp_path: Path):
        """A 403 response is authorization, not authentication -- no reauth."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        middleware = AuthMiddleware(key_store=store)

        response = httpx.Response(
            status_code=403,
            request=httpx.Request("GET", "https://seller1.example.com/api/products"),
        )
        result = middleware.handle_response(response)
        assert result.needs_reauth is False


# ---------------------------------------------------------------------------
# Booking-path credential wiring
#
# Regression coverage for the wiring gap where MultiSellerOrchestrator's
# quote/negotiation/booking call sites constructed DealsClient via the
# deals_client_factory with zero kwargs, so no ApiKeyStore credential ever
# reached an outbound request regardless of what was staged in the store.
# These exercise the SAME factory shape production uses
# (`lambda seller_url, **kwargs: DealsClient(seller_url, **kwargs)`, as in
# flows/deal_booking_flow.py::build_default_orchestrator and
# interfaces/chat/main.py::ChatInterface._make_deals_client) so a
# regression here fails for the same reason it would fail against a real
# seller.
# ---------------------------------------------------------------------------


def _production_deals_client_factory(seller_url: str, **kwargs):
    """Byte-identical in shape to the factory lambdas used in production."""
    return DealsClient(seller_url, **kwargs)


class TestBookingCredentialWiring:
    """MultiSellerOrchestrator must attach a stored per-seller key to the
    DealsClient used for quoting, negotiation, and booking."""

    def test_seller_with_stored_key_sends_x_api_key_header(self, tmp_path: Path):
        """A DealsClient built through the booking factory for a seller
        WITH a stored key must send the seller's expected X-Api-Key header."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("http://ctv-seller:8101", "ask_live_abc123")
        orchestrator = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=_production_deals_client_factory,
            key_store=store,
        )

        client = orchestrator._client_for_booking("http://ctv-seller:8101")

        assert client._client.headers.get("x-api-key") == "ask_live_abc123"

    def test_seller_with_no_stored_key_still_constructs_uncredentialed(self, tmp_path: Path):
        """A DealsClient built for a seller with NO stored key must still
        construct successfully and carry no credential -- identical to
        behavior before the credential wiring existed."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")  # empty store
        orchestrator = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=_production_deals_client_factory,
            key_store=store,
        )

        client = orchestrator._client_for_booking("http://display-seller:8102")

        assert "x-api-key" not in client._client.headers
        assert "authorization" not in client._client.headers

    def test_url_normalization_round_trips_through_booking_lookup(self, tmp_path: Path):
        """A key stored under a seller URL must be found by the exact
        lookup the booking path performs, including when the stored URL
        and the URL the orchestrator looks up with differ only by a
        trailing slash (the shape ApiKeyStore normalizes)."""
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("http://linear-seller:8103/", "ask_live_xyz789")
        orchestrator = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=_production_deals_client_factory,
            key_store=store,
        )

        # Seller URLs flowing through discovery/orchestration are used
        # without a trailing slash (matching DealsClient.seller_url,
        # which is itself rstrip("/")'d).
        client = orchestrator._client_for_booking("http://linear-seller:8103")

        assert client._client.headers.get("x-api-key") == "ask_live_xyz789"
