# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for client-side API key authentication (outbound to sellers)."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ad_buyer.auth.key_store import ApiKeyStore
from ad_buyer.auth.middleware import AuthMiddleware
from ad_buyer.clients.deals_client import DealsClient
from ad_buyer.orchestration import multi_seller
from ad_buyer.orchestration.multi_seller import DealParams, MultiSellerOrchestrator
from ad_buyer.registry.models import AgentCapability, AgentCard, TrustLevel

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


# ---------------------------------------------------------------------------
# Call-site coverage
#
# The tests above prove _client_for_booking attaches the credential. They do
# NOT prove the quote/negotiation/booking paths actually CALL it, and that
# distinction is the whole bug: before this wiring existed, _client_for_booking
# was absent and every path built an uncredentialed client directly from the
# factory. Reverting any single call site to
# ``self._deals_client_factory(seller_url)`` reintroduces the identical 401
# while leaving the helper, and its tests, perfectly green.
#
# So one test drives a real public entry point end to end, and one asserts
# structurally that the factory has exactly one caller. The structural test is
# the one that also covers call sites nobody has written yet.
# ---------------------------------------------------------------------------


class TestBookingCredentialCallSites:
    """The orchestration paths must route through the credentialed helper."""

    @pytest.mark.asyncio
    async def test_quote_path_sends_stored_key_to_the_factory(self, tmp_path: Path):
        """request_quotes_parallel must build its client with the stored key.

        Drives the public entry point rather than the helper, so the test
        fails if the quote call site stops routing through
        ``_client_for_booking``.
        """
        store = ApiKeyStore(store_path=tmp_path / "keys.json")
        store.add_key("http://seller-a.example.com", "ask_live_quote_path")

        factory_calls: list[tuple[str, dict]] = []

        def _spy_factory(seller_url: str, **kwargs):
            factory_calls.append((seller_url, kwargs))
            quote = MagicMock()
            # None keeps the orchestrator's CPM log on its "unavailable"
            # branch; a MagicMock here would blow up the f-string format.
            quote.pricing.final_cpm = None
            quote.quote_id = "q-001"
            client = MagicMock()
            client.request_quote = AsyncMock(return_value=quote)
            return client

        orchestrator = MultiSellerOrchestrator(
            registry_client=AsyncMock(),
            deals_client_factory=_spy_factory,
            key_store=store,
        )
        seller = AgentCard(
            agent_id="seller-a",
            name="Seller A",
            url="http://seller-a.example.com",
            protocols=["a2a", "deals-api-v1"],
            capabilities=[AgentCapability(name="ctv", description="ctv inventory")],
            trust_level=TrustLevel.VERIFIED,
        )
        deal_params = DealParams(
            product_id="prod-ctv-001",
            deal_type="PD",
            impressions=500_000,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )

        await orchestrator.request_quotes_parallel([seller], deal_params)

        assert len(factory_calls) == 1
        seller_url, kwargs = factory_calls[0]
        assert seller_url == "http://seller-a.example.com"
        assert kwargs.get("api_key") == "ask_live_quote_path"

    def test_deals_client_factory_has_exactly_one_caller(self):
        """``self._deals_client_factory`` may only be called from the helper.

        Covers the negotiation and booking call sites, plus any future one,
        without having to stand up their full orchestration state. A new
        uncredentialed call site anywhere in the module fails this test.
        """
        source = Path(multi_seller.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        callers: set[str] = set()
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "_deals_client_factory"
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    callers.add(func.name)

        assert callers == {"_client_for_booking"}, (
            "self._deals_client_factory must only be called from "
            "_client_for_booking, which attaches the per-seller credential. "
            f"Found callers: {sorted(callers)}. A client built straight from "
            "the factory carries no credential and 401s at a seller running "
            "seller-agent #77."
        )
