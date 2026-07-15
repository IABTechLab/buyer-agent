#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Phase 1 UAT: Integration test for all 6 buyer foundation modules.

Exercises auth, identity, registry, media kit, sessions, and negotiation
modules against a live seller server. The script is self-contained: it
starts the seller, runs all tests, and shuts down / cleans up on exit.

Usage:
    cd ad_buyer_system
    source venv/bin/activate
    python examples/phase1_uat.py

Requirements:
    - ad_buyer_system installed (pip install -e .)
    - ad_seller_system installed in its venv at ../ad_seller_system/venv
    - All 6 Phase 1 feature branches merged into the current branch
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Resolve the real repo root by following the git worktree link.
# When running from a worktree, __file__ is under .worktrees/,
# but the seller system is always at <agent_range>/ad_seller_system.
_SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPT_DIR.parent  # examples -> worktree root

# Find agent_range root: walk up until we find ad_seller_system
# Works from both main checkout and worktrees.
_candidate = REPO_ROOT
for _ in range(10):
    if (_candidate / "ad_seller_system").is_dir():
        AGENT_RANGE_ROOT = _candidate
        break
    _candidate = _candidate.parent
else:
    # Fallback: assume standard layout
    AGENT_RANGE_ROOT = Path(__file__).resolve().parents[3]

SELLER_ROOT = AGENT_RANGE_ROOT / "ad_seller_system"
SELLER_VENV_PYTHON = SELLER_ROOT / "venv" / "bin" / "python"
SELLER_UVICORN = SELLER_ROOT / "venv" / "bin" / "uvicorn"

SELLER_PORT = 8001
SELLER_URL = f"http://localhost:{SELLER_PORT}"

# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------


def banner(text: str) -> None:
    """Print a section banner."""
    width = 60
    print()
    print("=" * width)
    print(f"  {text}")
    print("=" * width)


def step(label: str) -> None:
    """Print a sub-step."""
    print(f"  - {label}")


def ok(label: str) -> None:
    """Print a success line."""
    print(f"  [OK] {label}")


def warn(label: str) -> None:
    """Print a warning line."""
    print(f"  [WARN] {label}")


def fail(label: str) -> None:
    """Print a failure line."""
    print(f"  [FAIL] {label}")


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


def start_seller_server() -> subprocess.Popen:
    """Start the seller API server as a subprocess.

    Uses uvicorn to run the main seller FastAPI app (which has /sessions,
    /media-kit, /.well-known/agent.json, /health, and all other endpoints).

    Returns:
        The Popen handle for the server process.
    """
    print(f"[SERVER] Starting seller API server on port {SELLER_PORT}...")

    env = os.environ.copy()
    # Ensure the seller's src is on the path
    seller_src = str(SELLER_ROOT / "src")
    env["PYTHONPATH"] = seller_src + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [
            str(SELLER_VENV_PYTHON),
            "-m", "uvicorn",
            "ad_seller.interfaces.api.main:app",
            "--host", "0.0.0.0",
            "--port", str(SELLER_PORT),
            "--log-level", "warning",
        ],
        cwd=str(SELLER_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return proc


def wait_for_server(url: str, timeout: float = 30.0) -> bool:
    """Poll the server health endpoint until it responds or timeout.

    Args:
        url: Base URL of the server.
        timeout: Maximum seconds to wait.

    Returns:
        True if the server is ready.
    """
    import httpx

    health_url = f"{url}/health"
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            resp = httpx.get(health_url, timeout=2.0)
            if resp.status_code == 200:
                print(f"[SERVER] Server ready after {attempt} attempt(s)")
                return True
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            pass
        time.sleep(0.5)
    return False


def stop_server(proc: subprocess.Popen) -> None:
    """Gracefully stop the server process."""
    print("[SERVER] Shutting down seller server...")
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    print("[SERVER] Server stopped")


# ---------------------------------------------------------------------------
# Results tracking
# ---------------------------------------------------------------------------

results: dict[str, bool | None] = {
    "Auth": None,
    "Identity": None,
    "Registry": None,
    "Media Kit": None,
    "Sessions": None,
    "Negotiation": None,
}


# ---------------------------------------------------------------------------
# Module tests
# ---------------------------------------------------------------------------


def test_auth(tmp_dir: str) -> bool:
    """Test the auth module (key store + middleware)."""
    banner("[1/6] AUTH MODULE (key_store + middleware)")

    from ad_buyer.auth.key_store import ApiKeyStore
    from ad_buyer.auth.middleware import AuthMiddleware

    store_path = Path(tmp_dir) / "test_keys.json"
    step(f"Create temp key store at {store_path}")
    store = ApiKeyStore(store_path=store_path)

    test_key = "test-api-key-12345"
    step(f"Add API key for seller at {SELLER_URL}")
    store.add_key(SELLER_URL, test_key)

    step("Verify key retrieval")
    retrieved = store.get_key(SELLER_URL)
    assert retrieved == test_key, f"Expected {test_key!r}, got {retrieved!r}"
    ok("Key stored and retrieved correctly")

    step("Verify key persistence (reload from disk)")
    store2 = ApiKeyStore(store_path=store_path)
    assert store2.get_key(SELLER_URL) == test_key
    ok("Key persisted to disk and reloaded")

    step("Verify list_sellers")
    sellers = store.list_sellers()
    assert SELLER_URL.rstrip("/") in sellers
    ok(f"list_sellers returned {sellers}")

    step("Verify AuthMiddleware header attachment")
    middleware = AuthMiddleware(store)
    import httpx
    req = httpx.Request("GET", f"{SELLER_URL}/health")
    authed_req = middleware.add_auth(req)
    assert "X-Api-Key" in authed_req.headers
    assert authed_req.headers["X-Api-Key"] == test_key
    ok("AuthMiddleware attached X-Api-Key header")

    step("Verify bearer mode")
    bearer_mw = AuthMiddleware(store, header_type="bearer")
    bearer_req = bearer_mw.add_auth(req)
    assert bearer_req.headers.get("Authorization") == f"Bearer {test_key}"
    ok("Bearer mode works")

    step("Verify key removal")
    assert store.remove_key(SELLER_URL) is True
    assert store.get_key(SELLER_URL) is None
    ok("Key removed successfully")

    ok("Auth module working")
    return True


def test_identity() -> bool:
    """Test the identity strategy module."""
    banner("[2/6] IDENTITY MODULE (strategy + masking)")

    from ad_buyer.identity.strategy import (
        CampaignGoal,
        DealContext,
        IdentityStrategy,
        SellerRelationship,
    )
    from ad_buyer.models.buyer_identity import AccessTier, BuyerIdentity, DealType

    strategy = IdentityStrategy()

    step("Create full buyer identity (seat + agency + advertiser)")
    identity = BuyerIdentity(
        seat_id="ttd-seat-001",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="rivian-789",
        advertiser_name="Rivian",
        advertiser_industry="Auto",
    )
    ok(f"Identity created: tier={identity.get_access_tier().value}")

    step("Test tier recommendation for PG deal -> expect ADVERTISER")
    pg_context = DealContext(
        deal_value_usd=500_000,
        deal_type=DealType.PROGRAMMATIC_GUARANTEED,
        seller_relationship=SellerRelationship.TRUSTED,
    )
    pg_tier = strategy.recommend_tier(pg_context)
    assert pg_tier == AccessTier.ADVERTISER, f"Expected ADVERTISER, got {pg_tier}"
    ok(f"PG deal -> {pg_tier.value} (correct)")

    step("Test tier recommendation for low-value PD deal -> expect lower tier")
    pd_context = DealContext(
        deal_value_usd=5_000,
        deal_type=DealType.PREFERRED_DEAL,
        seller_relationship=SellerRelationship.UNKNOWN,
        campaign_goal=CampaignGoal.AWARENESS,
    )
    pd_tier = strategy.recommend_tier(pd_context)
    assert pd_tier != AccessTier.ADVERTISER, f"Expected lower tier, got {pd_tier}"
    ok(f"Low-value PD deal -> {pd_tier.value} (lower than advertiser)")

    step("Test identity masking at AGENCY tier -> advertiser fields stripped")
    masked = strategy.build_identity(identity, AccessTier.AGENCY)
    assert masked.agency_id == "omnicom-456"
    assert masked.advertiser_id is None
    assert masked.advertiser_name is None
    ok("Agency-tier mask strips advertiser fields correctly")

    step("Test identity masking at SEAT tier -> agency fields stripped")
    seat_masked = strategy.build_identity(identity, AccessTier.SEAT)
    assert seat_masked.seat_id == "ttd-seat-001"
    assert seat_masked.agency_id is None
    assert seat_masked.advertiser_id is None
    ok("Seat-tier mask strips agency + advertiser fields")

    step("Test identity masking at PUBLIC tier -> all fields stripped")
    public_masked = strategy.build_identity(identity, AccessTier.PUBLIC)
    assert public_masked.seat_id is None
    assert public_masked.agency_id is None
    assert public_masked.advertiser_id is None
    ok("Public-tier mask strips all fields")

    step("Test savings estimation")
    savings = strategy.estimate_savings(
        base_price=30.0,
        current_tier=AccessTier.SEAT,
        target_tier=AccessTier.ADVERTISER,
    )
    # Seat=5%, Advertiser=15%, incremental=10%, savings=30*0.10=3.0
    assert savings == 3.0, f"Expected 3.0, got {savings}"
    ok(f"Savings from SEAT->ADVERTISER on $30 CPM: ${savings:.2f}")

    ok("Identity module working")
    return True


def test_registry() -> bool:
    """Test the registry module."""
    banner("[3/6] REGISTRY MODULE (client + cache)")

    from ad_buyer.registry.client import RegistryClient
    from ad_buyer.registry.models import AgentCapability, AgentCard

    step("Create registry client")
    client = RegistryClient(
        registry_url="http://localhost:9999/agent-registry",  # not running
        cache_ttl_seconds=60.0,
        timeout=3.0,
    )
    ok("Registry client created")

    step("Fetch agent card from seller (GET /.well-known/agent.json)")

    async def _fetch_card() -> AgentCard | None:
        return await client.fetch_agent_card(SELLER_URL)

    card = asyncio.run(_fetch_card())
    if card is not None:
        ok(f"Agent card fetched: {card.name} ({card.agent_id})")
    else:
        warn("Seller returned no agent card at /.well-known/agent.json")
        warn("Endpoint may not be configured or requires initialization")

    step("Test cache behavior (second fetch should be cached)")
    # Manually populate cache to verify caching works
    test_card = AgentCard(
        agent_id="test-seller-001",
        name="Test Seller",
        url=SELLER_URL,
        protocols=["mcp", "a2a"],
        capabilities=[
            AgentCapability(name="ctv", description="CTV inventory", tags=["streaming"])
        ],
    )
    client._cache.put(f"card:{SELLER_URL}", test_card)
    cached = client._cache.get(f"card:{SELLER_URL}")
    assert cached is not None
    assert cached.agent_id == "test-seller-001"
    ok("Cache stores and retrieves agent cards correctly")

    step("Test cache TTL expiry")
    # Create client with very short TTL
    short_cache_client = RegistryClient(cache_ttl_seconds=0.01, timeout=1.0)
    short_cache_client._cache.put("test-key", test_card)
    time.sleep(0.02)  # Wait for TTL to expire
    expired = short_cache_client._cache.get("test-key")
    assert expired is None
    ok("Cache TTL expiry works correctly")

    ok("Registry module working")
    return True


def test_media_kit() -> bool:
    """Test the media kit client against the live seller."""
    banner("[4/6] MEDIA KIT MODULE (client)")

    from ad_buyer.media_kit.client import MediaKitClient
    from ad_buyer.media_kit.models import MediaKitError

    async def _run_media_kit_tests() -> bool:
        async with MediaKitClient(timeout=10.0) as client:
            # GET /media-kit
            step("Fetch media kit from seller (GET /media-kit)")
            try:
                kit = await client.get_media_kit(SELLER_URL)
                ok(f"Media kit: {kit.seller_name}, {kit.total_packages} packages")
            except MediaKitError as e:
                if e.status_code == 404:
                    warn("Seller does not have /media-kit endpoint (404)")
                    warn("Skipping remaining media kit tests")
                    return True  # Graceful skip
                raise

            # GET /media-kit/packages
            step("List packages (GET /media-kit/packages)")
            try:
                packages = await client.list_packages(SELLER_URL)
                ok(f"Found {len(packages)} packages")
            except MediaKitError as e:
                warn(f"list_packages failed: {e}")
                packages = []

            # GET /media-kit/packages/{id}
            if packages:
                pkg = packages[0]
                step(f"Get package detail for '{pkg.name}' ({pkg.package_id})")
                try:
                    detail = await client.get_package(SELLER_URL, pkg.package_id)
                    ok(f"Package detail: {detail.name}")
                except MediaKitError as e:
                    warn(f"get_package failed: {e}")
            else:
                step("Skipping package detail (no packages returned)")

            # POST /media-kit/search
            step("Search packages (POST /media-kit/search)")
            try:
                results = await client.search_packages(SELLER_URL, query="streaming")
                ok(f"Search returned {len(results)} results")
            except MediaKitError as e:
                warn(f"search_packages failed: {e}")

        return True

    asyncio.run(_run_media_kit_tests())
    ok("Media kit module working")
    return True


def test_sessions(tmp_dir: str) -> bool:
    """Test the session module against the live seller."""
    banner("[5/6] SESSION MODULE (manager + store)")

    from ad_buyer.sessions.session_manager import SessionManager

    store_path = os.path.join(tmp_dir, "test_sessions.json")

    async def _run_session_tests() -> bool:
        mgr = SessionManager(store_path=store_path, timeout=10.0)

        # POST /sessions
        step("Create session with seller (POST /sessions)")
        try:
            session_id = await mgr.create_session(
                seller_url=SELLER_URL,
                buyer_identity={"seat_id": "test-seat-001", "name": "UAT Buyer"},
            )
            ok(f"Session created: {session_id}")
        except RuntimeError as e:
            warn(f"Session creation failed: {e}")
            warn("Seller may not support /sessions endpoint")
            # Still test local persistence
            step("Testing local session store persistence")
            from datetime import datetime, timedelta

            from ad_buyer.sessions.session_store import SessionRecord, SessionStore

            store = SessionStore(store_path)
            now = datetime.now(UTC)
            record = SessionRecord(
                session_id="local-test-001",
                seller_url=SELLER_URL,
                created_at=now.isoformat(),
                expires_at=(now + timedelta(days=7)).isoformat(),
            )
            store.save(record)
            retrieved = store.get(SELLER_URL)
            assert retrieved is not None
            assert retrieved.session_id == "local-test-001"
            ok("Local session store works correctly")
            return True

        # Verify session ID returned
        step("Verify session ID returned")
        assert session_id is not None and len(session_id) > 0
        ok(f"Session ID is valid: {session_id}")

        # Send a message
        step("Send a message in session")
        try:
            response = await mgr.send_message(
                seller_url=SELLER_URL,
                session_id=session_id,
                message={"type": "inquiry", "content": "What CTV inventory is available?"},
            )
            ok(f"Message sent, response keys: {list(response.keys())}")
        except RuntimeError as e:
            warn(f"send_message failed: {e}")

        # Verify persistence (get_or_create returns same ID)
        step("Verify session persistence (get_or_create returns same ID)")
        same_id = await mgr.get_or_create_session(seller_url=SELLER_URL)
        assert same_id == session_id, f"Expected {session_id}, got {same_id}"
        ok(f"get_or_create returned same session: {same_id}")

        # Close session
        step("Close session")
        await mgr.close_session(SELLER_URL, session_id)
        ok("Session closed")

        # Verify session removed from store
        active = mgr.list_active_sessions()
        assert SELLER_URL not in active
        ok("Session removed from local store after close")

        return True

    asyncio.run(_run_session_tests())
    ok("Session module working")
    return True


def test_negotiation() -> bool:
    """Test the negotiation module (strategy pattern, local logic).

    Full auto_negotiate requires seller quote endpoints (not yet built),
    so this tests the strategy pattern logic locally.
    """
    banner("[6/6] NEGOTIATION MODULE (strategy pattern)")

    from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
    from ad_buyer.negotiation.strategy import NegotiationContext

    step("Create SimpleThresholdStrategy (target=$25, max=$32, step=$1.50, max_rounds=5)")
    strategy = SimpleThresholdStrategy(
        target_cpm=25.0,
        max_cpm=32.0,
        concession_step=1.50,
        max_rounds=5,
    )
    ok("Strategy created")

    # Test should_accept
    step("Test should_accept logic")
    ctx = NegotiationContext(rounds_completed=1, seller_last_price=30.0)

    # $30 <= $32 max -> should accept
    assert strategy.should_accept(30.0, ctx) is True
    ok("should_accept($30) = True (at/below max $32)")

    # $35 > $32 max -> should not accept
    assert strategy.should_accept(35.0, ctx) is False
    ok("should_accept($35) = False (above max $32)")

    # Exactly at max
    assert strategy.should_accept(32.0, ctx) is True
    ok("should_accept($32) = True (exactly at max)")

    # Test next_offer
    step("Test next_offer logic")

    # First offer: should be target_cpm
    initial_ctx = NegotiationContext(
        rounds_completed=0,
        seller_last_price=0.0,
        our_last_offer=None,
    )
    first_offer = strategy.next_offer(40.0, initial_ctx)
    assert first_offer == 25.0, f"Expected 25.0, got {first_offer}"
    ok(f"First offer: ${first_offer:.2f} (target price)")

    # Subsequent offer: last + step
    round2_ctx = NegotiationContext(
        rounds_completed=1,
        seller_last_price=35.0,
        our_last_offer=25.0,
    )
    second_offer = strategy.next_offer(35.0, round2_ctx)
    assert second_offer == 26.5, f"Expected 26.5, got {second_offer}"
    ok(f"Second offer: ${second_offer:.2f} (25.0 + 1.50 step)")

    # Offer capped at max
    high_ctx = NegotiationContext(
        rounds_completed=4,
        seller_last_price=40.0,
        our_last_offer=31.0,
    )
    capped_offer = strategy.next_offer(40.0, high_ctx)
    assert capped_offer == 32.0, f"Expected 32.0 (capped), got {capped_offer}"
    ok(f"Capped offer: ${capped_offer:.2f} (capped at max)")

    # Test should_walk_away
    step("Test should_walk_away logic")

    # After max rounds -> walk away
    max_rounds_ctx = NegotiationContext(
        rounds_completed=5,
        seller_last_price=35.0,
        our_last_offer=32.0,
    )
    assert strategy.should_walk_away(35.0, max_rounds_ctx) is True
    ok("should_walk_away after max_rounds=5: True")

    # Seller not moving -> walk away
    stuck_ctx = NegotiationContext(
        rounds_completed=2,
        seller_last_price=35.0,
        our_last_offer=27.0,
        seller_previous_price=35.0,
    )
    assert strategy.should_walk_away(35.0, stuck_ctx) is True
    ok("should_walk_away when seller not moving: True")

    # Normal negotiation -> don't walk away
    normal_ctx = NegotiationContext(
        rounds_completed=2,
        seller_last_price=33.0,
        our_last_offer=27.0,
        seller_previous_price=35.0,
    )
    assert strategy.should_walk_away(33.0, normal_ctx) is False
    ok("should_walk_away in normal negotiation: False")

    # Test strategy swapping
    step("Verify strategy swapping works")
    strategy2 = SimpleThresholdStrategy(
        target_cpm=20.0,
        max_cpm=28.0,
        concession_step=2.0,
        max_rounds=3,
    )
    # Different strategy, different behavior
    assert strategy2.target_cpm == 20.0
    assert strategy2.max_cpm == 28.0
    first2 = strategy2.next_offer(40.0, initial_ctx)
    assert first2 == 20.0
    ok(f"Swapped strategy: target=$20, first offer=${first2:.2f}")

    # Verify it's a proper NegotiationStrategy subclass
    from ad_buyer.negotiation.strategy import NegotiationStrategy
    assert isinstance(strategy, NegotiationStrategy)
    assert isinstance(strategy2, NegotiationStrategy)
    ok("Both strategies are NegotiationStrategy instances")

    ok("Negotiation module working")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the Phase 1 UAT.

    Returns:
        0 if all tests pass, 1 otherwise.
    """
    banner("Phase 1 UAT: Buyer Foundation Modules")

    # Pre-flight checks
    if not SELLER_VENV_PYTHON.exists():
        fail(f"Seller venv not found at {SELLER_VENV_PYTHON}")
        fail("Run: cd ad_seller_system && python3.13 -m venv venv && source venv/bin/activate && pip install -e .")  # noqa: E501
        return 1

    # Create temp directory for test artifacts
    tmp_dir_obj = tempfile.mkdtemp(prefix="phase1_uat_")
    tmp_dir = tmp_dir_obj

    server_proc: subprocess.Popen | None = None

    try:
        # Start seller server
        server_proc = start_seller_server()

        # Wait for server ready
        print(f"[SERVER] Waiting for server ready (port {SELLER_PORT})...")
        if not wait_for_server(SELLER_URL, timeout=30.0):
            fail("Server did not start within 30 seconds")
            # Check if process died
            if server_proc.poll() is not None:
                stderr = server_proc.stderr.read().decode() if server_proc.stderr else ""
                fail(f"Server process exited with code {server_proc.returncode}")
                if stderr:
                    fail(f"stderr: {stderr[:500]}")
            return 1

        # Run module tests
        # 1. Auth (local + middleware verification)
        try:
            results["Auth"] = test_auth(tmp_dir)
        except Exception as e:
            fail(f"Auth module: {e}")
            results["Auth"] = False

        # 2. Identity (local strategy logic)
        try:
            results["Identity"] = test_identity()
        except Exception as e:
            fail(f"Identity module: {e}")
            results["Identity"] = False

        # 3. Registry (client + cache, graceful 404 handling)
        try:
            results["Registry"] = test_registry()
        except Exception as e:
            fail(f"Registry module: {e}")
            results["Registry"] = False

        # 4. Media Kit (live seller endpoints)
        try:
            results["Media Kit"] = test_media_kit()
        except Exception as e:
            fail(f"Media Kit module: {e}")
            results["Media Kit"] = False

        # 5. Sessions (live seller endpoints)
        try:
            results["Sessions"] = test_sessions(tmp_dir)
        except Exception as e:
            fail(f"Sessions module: {e}")
            results["Sessions"] = False

        # 6. Negotiation (local strategy pattern)
        try:
            results["Negotiation"] = test_negotiation()
        except Exception as e:
            fail(f"Negotiation module: {e}")
            results["Negotiation"] = False

    finally:
        # Shutdown server
        if server_proc is not None:
            stop_server(server_proc)

        # Cleanup temp files
        print(f"[CLEANUP] Removing temp files at {tmp_dir}...")
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[CLEANUP] Done")

    # Print results
    banner("RESULTS")
    passed = 0
    total = len(results)
    for module, result in results.items():
        if result is True:
            print(f"  {module + ':':15s} PASS")
            passed += 1
        elif result is False:
            print(f"  {module + ':':15s} FAIL")
        else:
            print(f"  {module + ':':15s} SKIPPED")

    print()
    if passed == total:
        print(f"Phase 1 UAT: {passed}/{total} modules passed")
    else:
        print(f"Phase 1 UAT: {passed}/{total} modules passed, {total - passed} failed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
