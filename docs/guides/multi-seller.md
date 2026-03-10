# Multi-Seller Discovery and Shopping

This guide walks through the process of discovering multiple sellers, comparing their inventory, and booking an optimal portfolio of deals across them. Working with multiple sellers gives you competitive pricing, broader reach, and the ability to assemble a media plan that no single seller could provide alone.

!!! info "Prerequisites"
    You need API keys for each seller you want authenticated access to. Without an API key you can still browse public media kits, but you will only see price ranges instead of exact pricing.

## Why Multi-Seller?

**Competitive pricing** -- Comparing packages across sellers ensures you are not overpaying. Sellers price differently based on their inventory mix, audience composition, and demand.

**Portfolio optimization** -- A CTV-focused seller may have premium living-room inventory but limited mobile reach. Combining packages from specialized sellers lets you build a balanced media plan.

**Risk diversification** -- Spreading spend across sellers protects against delivery shortfalls. If one seller under-delivers, the others can absorb the gap.

**Negotiation leverage** -- When sellers know you are shopping competitively, they are more likely to offer favorable terms. The buyer agent's identity strategy lets you control exactly how much you reveal to each seller.

## Step-by-Step Workflow

### Step 1: Discover Sellers via the Registry

Use `RegistryClient` to query the IAB AAMP agent registry for seller agents. You can filter by capability to narrow results to sellers that carry the inventory types you need.

```python
from ad_buyer.registry.client import RegistryClient

registry = RegistryClient(
    registry_url="http://localhost:8080/agent-registry",
    cache_ttl_seconds=300,
    timeout=15.0,
)

# Discover all sellers
all_sellers = await registry.discover_sellers()

# Or filter by capability (e.g., CTV inventory)
ctv_sellers = await registry.discover_sellers(
    capabilities_filter=["ctv"]
)

for seller in ctv_sellers:
    print(f"{seller.name} ({seller.url})")
    print(f"  Protocols: {seller.protocols}")
    print(f"  Capabilities: {[c.name for c in seller.capabilities]}")
```

`discover_sellers` returns a list of `AgentCard` objects. Each card contains the seller's URL, supported protocols (MCP, A2A, OpenDirect), and declared capabilities.

!!! tip "Direct Agent Card Fetch"
    If you already know a seller's URL, you can fetch their agent card directly without going through the registry:

    ```python
    card = await registry.fetch_agent_card("https://seller.example.com")
    ```

### Step 2: Verify Seller Capabilities

Before sending any buyer data to a seller, verify their trust status in the registry. The trust level tells you how much the registry operator has vetted them.

```python
from ad_buyer.registry.models import TrustLevel

for seller in ctv_sellers:
    trust_info = await registry.verify_agent(seller.url)

    print(f"{seller.name}: {trust_info.trust_level.value}")
    print(f"  Registered: {trust_info.is_registered}")
    if trust_info.registry_id:
        print(f"  Registry ID: {trust_info.registry_id}")
```

**Trust levels and what they mean:**

| Trust Level | Meaning | Recommendation |
|-------------|---------|----------------|
| `preferred` | Strategic partner verified by the registry operator | Safe for full identity disclosure |
| `verified` | Identity and inventory verified | Safe for agency-tier disclosure |
| `registered` | Self-registered, not independently verified | Start with seat-tier disclosure |
| `unknown` | Not found in the registry | Browse public media kit only |
| `blocked` | Explicitly blocked by the registry | Do not interact |

Filter out any sellers you do not trust:

```python
trusted_sellers = [
    seller for seller in ctv_sellers
    if (await registry.verify_agent(seller.url)).trust_level
    not in (TrustLevel.UNKNOWN, TrustLevel.BLOCKED)
]
```

### Step 3: Browse Media Kits Across Sellers

Use `MediaKitClient` to fetch inventory from all trusted sellers in parallel. The `aggregate_across_sellers` method handles concurrent requests and silently skips sellers that are unreachable.

```python
from ad_buyer.media_kit.client import MediaKitClient

async with MediaKitClient(api_key="your-api-key") as media_client:
    seller_urls = [s.url for s in trusted_sellers]

    # Fetch all packages from all sellers in parallel
    all_packages = await media_client.aggregate_across_sellers(seller_urls)

    print(f"Found {len(all_packages)} packages across {len(seller_urls)} sellers")
    for pkg in all_packages:
        print(f"  [{pkg.seller_url}] {pkg.name} -- {pkg.price_range}")
```

For a richer view of a specific seller, fetch their full media kit:

```python
    kit = await media_client.get_media_kit("http://seller-a.example.com:8001")

    print(f"Seller: {kit.seller_name}")
    print(f"Total packages: {kit.total_packages}")
    print(f"Featured packages: {len(kit.featured)}")

    for pkg in kit.featured:
        print(f"  {pkg.name} -- {pkg.price_range} ({pkg.ad_formats})")
```

You can also search a seller's inventory by keyword:

```python
    from ad_buyer.media_kit.models import SearchFilter

    results = await media_client.search_packages(
        seller_url="http://seller-a.example.com:8001",
        query="premium video",
        filters=SearchFilter(buyer_tier="agency", agency_id="omnicom-456"),
    )
```

### Step 4: Compare Packages and Pricing

With packages aggregated, build a comparison view. Authenticated requests return `PackageDetail` objects with exact pricing; unauthenticated requests return `PackageSummary` with price ranges.

```python
    # Get detailed pricing for packages of interest
    shortlist = []
    for pkg in all_packages:
        if "video" in pkg.ad_formats:
            detail = await media_client.get_package(pkg.seller_url, pkg.package_id)
            shortlist.append(detail)

    # Sort by exact price (authenticated) or fall back to name
    shortlist.sort(key=lambda p: getattr(p, "exact_price", None) or float("inf"))

    print("=== Video Package Comparison ===")
    for pkg in shortlist:
        price = getattr(pkg, "exact_price", None)
        floor = getattr(pkg, "floor_price", None)
        neg = getattr(pkg, "negotiation_enabled", False)
        print(f"  {pkg.name} @ {pkg.seller_url}")
        print(f"    Price: ${price} CPM" if price else f"    Range: {pkg.price_range}")
        if floor:
            print(f"    Floor: ${floor} CPM")
        print(f"    Negotiable: {'Yes' if neg else 'No'}")
        print(f"    Formats: {pkg.ad_formats}")
        print()
```

**Key fields for comparison:**

| Field | Available | Description |
|-------|-----------|-------------|
| `price_range` | Always | Human-readable range (e.g., "$28-$42 CPM") |
| `exact_price` | Authenticated | Exact CPM for this buyer tier |
| `floor_price` | Authenticated | Minimum acceptable price |
| `negotiation_enabled` | Authenticated | Whether the seller accepts counter-offers |
| `volume_discounts_available` | Authenticated | Whether volume discounts apply |
| `ad_formats` | Always | Supported ad formats |
| `geo_targets` | Always | Geographic targeting |
| `device_types` | Always | Device type codes |

### Step 5: Request Quotes from Multiple Sellers

For packages that support negotiation, use the `NegotiationClient` to request quotes. Run negotiations in parallel across sellers to save time.

```python
import asyncio
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy

strategy = SimpleThresholdStrategy(
    target_cpm=20.0,
    max_cpm=30.0,
    concession_step=2.0,
    max_rounds=5,
)

async def negotiate_with_seller(seller_url, proposal_id):
    """Run a negotiation with a single seller."""
    client = NegotiationClient(api_key="your-seller-api-key")
    try:
        result = await client.auto_negotiate(
            seller_url=seller_url,
            proposal_id=proposal_id,
            strategy=strategy,
        )
        return result
    except Exception as e:
        print(f"Negotiation failed with {seller_url}: {e}")
        return None

# Negotiate with all sellers concurrently
tasks = [
    negotiate_with_seller(pkg.seller_url, f"quote-{pkg.package_id}")
    for pkg in shortlist
    if getattr(pkg, "negotiation_enabled", False)
]

results = await asyncio.gather(*tasks)

for result in results:
    if result and result.outcome.value == "accepted":
        print(f"  Deal at ${result.final_price} CPM ({result.rounds_count} rounds)")
```

!!! warning "Negotiation Tier Requirement"
    Only **Agency** and **Advertiser** tier buyers can negotiate. If your identity is at Public or Seat tier, the seller will reject negotiation attempts. See [Identity Strategy Across Sellers](#identity-strategy-across-sellers) below.

### Step 6: Evaluate and Select Optimal Portfolio

With quotes in hand, score each option and select the combination that best fits your campaign goals and budget.

```python
def score_package(pkg, negotiation_result):
    """Score a package based on price, reach, and negotiation outcome."""
    score = 0.0

    # Price score: lower is better
    price = negotiation_result.final_price if negotiation_result else getattr(pkg, "exact_price", None)
    if price:
        score += max(0, 50 - price)  # Favor lower CPMs

    # Format diversity bonus
    if "video" in pkg.ad_formats:
        score += 10
    if "display" in pkg.ad_formats:
        score += 5

    # Negotiability bonus (future flexibility)
    if getattr(pkg, "negotiation_enabled", False):
        score += 5

    # Volume discount bonus
    if getattr(pkg, "volume_discounts_available", False):
        score += 5

    return score

# Score and rank
scored = []
for pkg, result in zip(shortlist, results):
    s = score_package(pkg, result)
    scored.append((s, pkg, result))

scored.sort(key=lambda x: x[0], reverse=True)

# Select top packages within budget
budget = 50_000.0
selected = []
remaining = budget
for score, pkg, result in scored:
    price = result.final_price if result else getattr(pkg, "exact_price", None)
    if price and remaining > 0:
        selected.append((pkg, result, price))
        remaining -= 10_000  # Example: allocate fixed amount per seller
        if remaining <= 0:
            break

print(f"Selected {len(selected)} packages, budget remaining: ${remaining}")
```

### Step 7: Book Deals with Selected Sellers

Submit booking requests for your selected packages. The bookings API runs the workflow in the background -- submit the brief and then poll for status.

```python
import httpx

async def book_deal(seller_url: str, brief: dict) -> str:
    """Submit a booking request to a seller and return the job ID."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{seller_url}/bookings",
            json=brief,
            headers={"X-API-Key": "your-api-key"},
        )
        response.raise_for_status()
        return response.json()["job_id"]

# Book with each selected seller
for pkg, result, price in selected:
    brief = {
        "brief": {
            "name": "Q3 Multi-Seller Campaign",
            "objectives": ["brand_awareness"],
            "budget": 10_000,
            "start_date": "2026-07-01",
            "end_date": "2026-09-30",
            "target_audience": {
                "demographics": {"age": "18-34"},
                "interests": ["gaming", "technology"],
            },
            "channels": pkg.ad_formats,
        },
        "auto_approve": False,
    }

    job_id = await book_deal(pkg.seller_url, brief)
    print(f"Booking submitted: {pkg.name} @ {pkg.seller_url} -> job {job_id}")
```

Poll for status and approve recommendations when they are ready:

```python
async def poll_and_approve(seller_url: str, job_id: str):
    """Poll a booking job until it needs approval, then approve all."""
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            resp = await client.get(
                f"{seller_url}/bookings/{job_id}",
                headers={"X-API-Key": "your-api-key"},
            )
            status = resp.json()
            if status["status"] == "awaiting_approval":
                # Review recommendations, then approve
                approve_resp = await client.post(
                    f"{seller_url}/bookings/{job_id}/approve-all",
                    headers={"X-API-Key": "your-api-key"},
                )
                return approve_resp.json()
            elif status["status"] in ("completed", "failed"):
                return status
            await asyncio.sleep(2)
```

## Identity Strategy Across Sellers

The `IdentityStrategy` class decides how much buyer identity to reveal to each seller. Revealing more identity unlocks better pricing, but it also exposes your buying patterns. When shopping multiple sellers, the strategy becomes especially important.

**Discount tiers:**

| Tier | Revealed | Discount |
|------|----------|----------|
| PUBLIC | Nothing | 0% |
| SEAT | DSP seat ID | 5% |
| AGENCY | Seat + agency ID | 10% |
| ADVERTISER | Seat + agency + advertiser ID | 15% |

### Use the Strategy to Decide Per Seller

```python
from ad_buyer.identity.strategy import IdentityStrategy, DealContext, SellerRelationship, CampaignGoal
from ad_buyer.models.buyer_identity import BuyerIdentity, AccessTier, DealType

strategy = IdentityStrategy(
    high_value_threshold_usd=100_000,
    mid_value_threshold_usd=25_000,
)

# Full identity (reveal all we have)
full_identity = BuyerIdentity(
    seat_id="ttd-seat-123",
    seat_name="The Trade Desk",
    agency_id="omnicom-456",
    agency_name="OMD",
    agency_holding_company="Omnicom",
    advertiser_id="coca-cola-789",
    advertiser_name="Coca-Cola",
    advertiser_industry="CPG",
)

# Different context for each seller
seller_a_context = DealContext(
    deal_value_usd=150_000,
    deal_type=DealType.PROGRAMMATIC_GUARANTEED,
    seller_relationship=SellerRelationship.TRUSTED,
    campaign_goal=CampaignGoal.PERFORMANCE,
)

seller_b_context = DealContext(
    deal_value_usd=10_000,
    deal_type=DealType.PREFERRED_DEAL,
    seller_relationship=SellerRelationship.UNKNOWN,
    campaign_goal=CampaignGoal.AWARENESS,
)

# Strategy recommends different tiers
tier_a = strategy.recommend_tier(seller_a_context)  # ADVERTISER (high value + trusted + PG)
tier_b = strategy.recommend_tier(seller_b_context)  # SEAT (low value + unknown)

# Build masked identities
identity_for_a = strategy.build_identity(full_identity, tier_a)
identity_for_b = strategy.build_identity(full_identity, tier_b)

print(f"Seller A sees: {identity_for_a.get_access_tier().value}")
# "advertiser" -- full identity, 15% discount
print(f"Seller B sees: {identity_for_b.get_access_tier().value}")
# "seat" -- only seat ID, 5% discount
```

### Estimate Savings from Upgrading Tier

Before deciding to reveal more identity to a seller, estimate the savings:

```python
savings = strategy.estimate_savings(
    base_price=35.0,        # $35 CPM base
    current_tier=AccessTier.SEAT,      # Currently at seat tier
    target_tier=AccessTier.AGENCY,     # Considering agency tier
)
print(f"Estimated savings: ${savings:.2f} per CPM")
# $1.75 per CPM (5% incremental discount on $35)
```

### Guidelines for Multi-Seller Identity

- **Start conservative.** Begin at SEAT tier with new sellers. Upgrade after you have established trust and confirmed their inventory quality.
- **Reveal more for high-value deals.** A $150K PG deal justifies full advertiser disclosure. A $5K preferred deal does not.
- **Use trusted sellers as anchors.** If a trusted seller offers $28 CPM at advertiser tier, use that as a benchmark when negotiating with others at lower tiers.
- **Never reveal advertiser identity to unregistered sellers.** If `trust_level` is `unknown`, cap disclosure at SEAT tier.

## Caching and Performance

The `RegistryClient` uses `SellerCache` to avoid redundant network calls. Understanding the cache behavior helps you tune performance for multi-seller workflows.

### How the Cache Works

`SellerCache` is an in-memory TTL cache. Every entry expires after `cache_ttl_seconds` (default: 300 seconds / 5 minutes).

| Method | What It Caches | Key Format |
|--------|---------------|------------|
| `discover_sellers()` | List of agent cards | `discover:{sorted_capabilities}` |
| `fetch_agent_card()` | Single agent card | `card:{agent_url}` |

Individual seller cards are also cached when returned as part of a `discover_sellers` call.

### Tuning the TTL

```python
# Short TTL for fast-changing environments (e.g., testing)
registry = RegistryClient(cache_ttl_seconds=30)

# Long TTL for stable production environments
registry = RegistryClient(cache_ttl_seconds=900)  # 15 minutes
```

### Forcing a Refresh

If you need fresh data (for example, after a seller updates their capabilities), invalidate the cache:

```python
# Clear a specific entry
registry._cache.invalidate("discover:ctv")

# Clear everything
registry._cache.clear()

# Then re-fetch
fresh_sellers = await registry.discover_sellers(capabilities_filter=["ctv"])
```

### Parallel Fetch Performance

`MediaKitClient.aggregate_across_sellers` uses `asyncio.gather` to fetch from all sellers concurrently. With 5 sellers and a 30-second timeout, the total wall-clock time is bounded by the slowest seller, not the sum of all sellers. Sellers that time out or error are silently skipped.

## Error Handling

Multi-seller workflows involve many network calls. Sellers may be down, slow, or returning errors. The buyer agent's clients are designed to degrade gracefully.

### Registry Errors

`RegistryClient.discover_sellers` returns an empty list on errors instead of raising exceptions. This lets your workflow continue even if the registry is temporarily unavailable.

```python
sellers = await registry.discover_sellers()
if not sellers:
    # Registry may be down -- fall back to known seller URLs
    print("Registry unavailable, using cached/known sellers")
    sellers = [
        AgentCard(agent_id="known-1", name="Seller A", url="http://seller-a:8001"),
        AgentCard(agent_id="known-2", name="Seller B", url="http://seller-b:8001"),
    ]
```

### Media Kit Errors

`MediaKitClient` raises `MediaKitError` on HTTP errors and connection failures. When aggregating, individual seller failures are caught and skipped:

```python
from ad_buyer.media_kit.models import MediaKitError

# aggregate_across_sellers handles errors internally
packages = await media_client.aggregate_across_sellers(seller_urls)
# If seller-b is down, you still get packages from seller-a and seller-c

# For individual calls, catch MediaKitError explicitly
try:
    detail = await media_client.get_package("http://seller-b:8001", "pkg-123")
except MediaKitError as e:
    print(f"Seller error: {e} (status {e.status_code})")
    # Skip this seller or retry later
```

### Negotiation Errors

Network errors during negotiation raise `httpx.HTTPStatusError`. Wrap each negotiation in a try/except so that a failure with one seller does not block the others:

```python
async def safe_negotiate(seller_url, proposal_id, strategy):
    """Negotiate with error handling."""
    client = NegotiationClient(api_key="your-api-key")
    try:
        return await client.auto_negotiate(seller_url, proposal_id, strategy)
    except httpx.HTTPStatusError as e:
        print(f"Negotiation HTTP error with {seller_url}: {e.response.status_code}")
        return None
    except httpx.ConnectError:
        print(f"Cannot reach {seller_url}")
        return None
    except httpx.TimeoutException:
        print(f"Negotiation timed out with {seller_url}")
        return None
```

### Timeout Tuning

Both clients accept a `timeout` parameter. For multi-seller workflows, keep timeouts tight so that one slow seller does not stall the entire pipeline:

```python
# Tight timeouts for discovery (fast lookups)
registry = RegistryClient(timeout=10.0)

# Moderate timeouts for media kit browsing
media_client = MediaKitClient(timeout=15.0)

# Generous timeouts for negotiations (multi-turn)
negotiation_client = NegotiationClient(timeout=30.0)
```

## Example: Complete Multi-Seller Workflow

This end-to-end example discovers sellers, compares their CTV inventory, negotiates with the best options, and books deals.

```python
import asyncio
from ad_buyer.registry.client import RegistryClient
from ad_buyer.registry.models import TrustLevel
from ad_buyer.media_kit.client import MediaKitClient
from ad_buyer.media_kit.models import MediaKitError
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy
from ad_buyer.identity.strategy import (
    IdentityStrategy, DealContext, SellerRelationship, CampaignGoal,
)
from ad_buyer.models.buyer_identity import BuyerIdentity, DealType


async def multi_seller_workflow():
    # --- Configuration ---
    budget = 50_000.0
    campaign_name = "Q3 CTV Campaign"
    target_cpm = 22.0
    max_cpm = 35.0

    full_identity = BuyerIdentity(
        seat_id="ttd-seat-123",
        seat_name="The Trade Desk",
        agency_id="omnicom-456",
        agency_name="OMD",
        agency_holding_company="Omnicom",
        advertiser_id="coca-cola-789",
        advertiser_name="Coca-Cola",
        advertiser_industry="CPG",
    )

    id_strategy = IdentityStrategy()
    neg_strategy = SimpleThresholdStrategy(
        target_cpm=target_cpm,
        max_cpm=max_cpm,
        concession_step=2.0,
        max_rounds=5,
    )

    # --- Step 1: Discover CTV sellers ---
    registry = RegistryClient()
    sellers = await registry.discover_sellers(capabilities_filter=["ctv"])
    print(f"Discovered {len(sellers)} CTV sellers")

    # --- Step 2: Verify trust ---
    verified_sellers = []
    for seller in sellers:
        trust = await registry.verify_agent(seller.url)
        if trust.trust_level not in (TrustLevel.UNKNOWN, TrustLevel.BLOCKED):
            verified_sellers.append(seller)
        else:
            print(f"  Skipping {seller.name} (trust: {trust.trust_level.value})")

    print(f"Verified {len(verified_sellers)} sellers")

    # --- Step 3: Browse media kits ---
    async with MediaKitClient(api_key="your-api-key") as media_client:
        seller_urls = [s.url for s in verified_sellers]
        all_packages = await media_client.aggregate_across_sellers(seller_urls)
        print(f"Found {len(all_packages)} packages total")

        # --- Step 4: Compare CTV packages ---
        ctv_packages = [
            pkg for pkg in all_packages
            if "video" in pkg.ad_formats or "ctv" in pkg.tags
        ]
        print(f"Filtered to {len(ctv_packages)} CTV-relevant packages")

        # Get detailed pricing
        detailed = []
        for pkg in ctv_packages:
            try:
                detail = await media_client.get_package(pkg.seller_url, pkg.package_id)
                detailed.append(detail)
            except MediaKitError:
                pass  # Skip packages we cannot fetch details for

    # --- Step 5: Negotiate with sellers that allow it ---
    negotiable = [
        pkg for pkg in detailed
        if getattr(pkg, "negotiation_enabled", False)
    ]
    print(f"Negotiating with {len(negotiable)} sellers")

    neg_results = {}
    for pkg in negotiable:
        client = NegotiationClient(api_key="your-api-key")
        try:
            result = await client.auto_negotiate(
                seller_url=pkg.seller_url,
                proposal_id=f"quote-{pkg.package_id}",
                strategy=neg_strategy,
            )
            neg_results[pkg.package_id] = result
            print(f"  {pkg.name}: {result.outcome.value} at ${result.final_price}")
        except Exception as e:
            print(f"  {pkg.name}: negotiation failed ({e})")

    # --- Step 6: Select portfolio ---
    scored = []
    for pkg in detailed:
        result = neg_results.get(pkg.package_id)
        price = (result.final_price if result and result.final_price
                 else getattr(pkg, "exact_price", None))
        if price:
            scored.append((price, pkg, result))

    scored.sort(key=lambda x: x[0])  # Cheapest first

    selected = []
    remaining = budget
    for price, pkg, result in scored:
        allocation = min(remaining, 15_000)  # Cap per seller
        if allocation > 0:
            selected.append((pkg, result, price, allocation))
            remaining -= allocation
        if remaining <= 0:
            break

    print(f"\nSelected {len(selected)} packages, ${budget - remaining:.0f} allocated")

    # --- Step 7: Book deals ---
    for pkg, result, price, allocation in selected:
        tier = id_strategy.recommend_tier(DealContext(
            deal_value_usd=allocation,
            deal_type=DealType.PREFERRED_DEAL,
            seller_relationship=SellerRelationship.NEW,
            campaign_goal=CampaignGoal.AWARENESS,
        ))
        identity = id_strategy.build_identity(full_identity, tier)
        print(f"  Booking {pkg.name} @ ${price} CPM")
        print(f"    Seller: {pkg.seller_url}")
        print(f"    Identity tier: {identity.get_access_tier().value}")
        print(f"    Allocation: ${allocation:,.0f}")
        # Submit booking via POST /bookings (see Step 7 above)

    print("\nWorkflow complete.")


# Run
asyncio.run(multi_seller_workflow())
```

## Related

- [Negotiation](negotiation.md) -- Detailed guide on negotiation strategies and manual step-by-step control
- [Media Kit API](../api/media-kit.md) -- Full API reference for browsing seller inventory
- [Bookings API](../api/bookings.md) -- Booking lifecycle and approval endpoints
- [Authentication](../api/authentication.md) -- API key setup for authenticated access
- [Seller Agent Guide](../integration/seller-agent.md) -- How to integrate with seller agents
