# Deal Booking

This guide walks you through the complete deal booking process -- from browsing a seller's inventory to getting a confirmed Deal ID you can activate in your DSP.

## Prerequisites

Before you start, you need:

- **A running seller agent** -- The seller's API must be reachable (e.g. `http://seller.example.com:8001`). See [Seller Agent Integration](../integration/seller-agent.md) for connection details.
- **An API key** -- Get one from the seller for authenticated access. Public browsing works without a key, but you need one to see exact pricing and book deals.
- **Python 3.11+** with the `ad_buyer` package installed.

!!! tip "Check your access tier"
    Your API key determines your access tier (PUBLIC, SEAT, AGENCY, ADVERTISER). Higher tiers unlock better pricing and negotiation rights. See [Authentication](../api/authentication.md) for details.

## Quick Example

Here's the minimum code to go from zero to a booked deal:

```python
import asyncio
from ad_buyer.clients.deals_client import DealsClient
from ad_buyer.models.deals import QuoteRequest, DealBookingRequest

async def book_a_deal():
    async with DealsClient(
        seller_url="http://seller.example.com:8001",
        api_key="your-api-key",
    ) as client:
        # 1. Request a quote
        quote = await client.request_quote(QuoteRequest(
            product_id="prod-premium-video",
            deal_type="PD",
            impressions=500_000,
        ))
        print(f"Quote: ${quote.pricing.final_cpm} CPM (expires: {quote.expires_at})")

        # 2. Book it
        deal = await client.book_deal(DealBookingRequest(
            quote_id=quote.quote_id,
        ))
        print(f"Deal ID: {deal.deal_id}")
        print(f"OpenRTB floor: ${deal.openrtb_params.bidfloor}")

asyncio.run(book_a_deal())
```

That's the core loop: **quote, then book**. The rest of this guide covers each step in detail, plus negotiation, persistence, and the higher-level flows.

---

## Step-by-Step Walkthrough

### Step 1: Authenticate with the Seller

Get an API key from the seller. The key maps to an access tier on the seller side:

| Tier | What You Reveal | What You Get |
|------|-----------------|--------------|
| PUBLIC | Nothing | Price ranges only |
| SEAT | DSP seat ID | ~5% discount, exact pricing |
| AGENCY | Agency ID | ~10% discount, negotiation rights |
| ADVERTISER | Advertiser ID | ~15% discount, negotiation rights |

Pass your key when creating the client:

```python
from ad_buyer.clients.deals_client import DealsClient

client = DealsClient(
    seller_url="http://seller.example.com:8001",
    api_key="your-api-key",
)
```

For bearer token auth instead:

```python
client = DealsClient(
    seller_url="http://seller.example.com:8001",
    bearer_token="your-bearer-token",
)
```

### Step 2: Browse Inventory (Media Kit)

Before requesting a quote, find what the seller has available. The `MediaKitClient` lets you browse the seller's catalog:

```python
from ad_buyer.media_kit import MediaKitClient

async with MediaKitClient(api_key="your-api-key") as mk:
    # Get the full catalog
    kit = await mk.get_media_kit("http://seller.example.com:8001")
    print(f"{kit.seller_name}: {kit.total_packages} packages")

    # Search for something specific
    results = await mk.search_packages(
        "http://seller.example.com:8001",
        query="premium video sports",
    )
    for pkg in results:
        print(f"  {pkg.name} — {pkg.price_range}")
        # Note the product_id for step 3
```

Look for two things on each package:

- **`negotiation_enabled`** -- If `True`, you can negotiate the price down (Step 4).
- **`product_id`** on the placements -- You'll need this to request a quote.

See [Media Kit](../api/media-kit.md) for the full API.

### Step 3: Request a Quote

A quote is a **non-binding price** from the seller. It locks in pricing for a limited time.

```python
from ad_buyer.models.deals import QuoteRequest, BuyerIdentityPayload

quote = await client.request_quote(QuoteRequest(
    product_id="prod-premium-video",
    deal_type="PD",                    # PD = Preferred Deal
    impressions=500_000,
    flight_start="2026-07-01",
    flight_end="2026-09-30",
    target_cpm=25.0,                   # Your ideal price (optional hint)
    buyer_identity=BuyerIdentityPayload(
        agency_id="omnicom-456",       # Reveal identity for better pricing
        advertiser_id="coca-cola",
    ),
))

print(f"Quote ID: {quote.quote_id}")
print(f"Base CPM: ${quote.pricing.base_cpm}")
print(f"Your price: ${quote.pricing.final_cpm} (tier: {quote.buyer_tier})")
print(f"Expires: {quote.expires_at}")
```

**Deal types:**

| Type | Code | Description |
|------|------|-------------|
| Preferred Deal | `PD` | First-look access at a fixed CPM, non-guaranteed |
| Programmatic Guaranteed | `PG` | Guaranteed volume at a fixed CPM |
| Private Auction | `PA` | Invitation-only auction with a floor price |

!!! warning "Quotes expire"
    The seller sets an expiry on each quote. Book the deal before it expires, or you'll need to request a new quote.

### Step 4: Negotiate (Optional)

If the seller supports negotiation for your tier and you want a better price, negotiate before booking.

```python
from ad_buyer.negotiation.client import NegotiationClient
from ad_buyer.negotiation.strategies.simple_threshold import SimpleThresholdStrategy

neg_client = NegotiationClient(api_key="your-api-key")

strategy = SimpleThresholdStrategy(
    target_cpm=20.0,        # Our opening offer
    max_cpm=30.0,           # Walk away above this
    concession_step=2.0,    # Give $2 per round
    max_rounds=5,
)

result = await neg_client.auto_negotiate(
    seller_url="http://seller.example.com:8001",
    proposal_id=quote.quote_id,  # Negotiate on the quote
    strategy=strategy,
)

if result.outcome.value == "accepted":
    print(f"Negotiated to ${result.final_price} CPM in {result.rounds_count} rounds")
else:
    print(f"Walked away — seller wouldn't budge below ${result.rounds[-1].seller_price}")
```

!!! info "Who can negotiate?"
    Only **Agency** and **Advertiser** tier buyers. PUBLIC and SEAT tier buyers skip this step. Check with `buyer_context.can_negotiate()`.

For full negotiation details, see [Negotiation](negotiation.md).

### Step 5: Book the Deal

Convert your quote into a confirmed deal:

```python
from ad_buyer.models.deals import DealBookingRequest

deal = await client.book_deal(DealBookingRequest(
    quote_id=quote.quote_id,
    buyer_identity=BuyerIdentityPayload(
        agency_id="omnicom-456",
        advertiser_id="coca-cola",
    ),
    notes="Q3 awareness campaign — premium video",
))

print(f"Deal ID: {deal.deal_id}")
print(f"Status: {deal.status}")
print(f"Final CPM: ${deal.pricing.final_cpm}")
print(f"Impressions: {deal.terms.impressions:,}")

# OpenRTB parameters for DSP activation
if deal.openrtb_params:
    print(f"OpenRTB Deal ID: {deal.openrtb_params.id}")
    print(f"Bid floor: ${deal.openrtb_params.bidfloor}")
```

The seller returns a `DealResponse` with:

- **`deal_id`** -- The seller's unique deal identifier.
- **`openrtb_params`** -- Plug these into your DSP's deal targeting. The `id` field is what goes into OpenRTB bid requests.
- **`activation_instructions`** -- Any seller-specific setup steps (e.g., creative specs, trafficking notes).

### Step 6: Track Your Deal

Use `get_deal` to check the current status of a deal:

```python
deal = await client.get_deal("deal-abc123")
print(f"Status: {deal.status}")  # proposed, active, rejected, expired, completed
```

For persistent tracking across sessions, attach a `DealStore`:

```python
from ad_buyer.storage.deal_store import DealStore

store = DealStore("sqlite:///./deals.db")
store.connect()

# Attach to the client — quotes and deals auto-persist
client = DealsClient(
    seller_url="http://seller.example.com:8001",
    api_key="your-api-key",
    deal_store=store,
)

# ... request quotes and book deals as normal ...

# Later, query your local store
all_deals = store.list_deals(status="booked")
for d in all_deals:
    print(f"  {d['product_name']} — ${d['price']} CPM — {d['status']}")

# Get negotiation history
rounds = store.get_negotiation_history(d["id"])

# Get status transition audit log
history = store.get_status_history("deal", d["id"])
```

The `DealStore` automatically records:

- **Quotes** as deals with status `quoted`
- **Booked deals** with status `booked`
- **Status transitions** with timestamps and trigger info
- **Negotiation rounds** (when recorded separately)
- **Booking records** for line items

---

## Using DealBookingFlow (Campaign-Level)

`DealBookingFlow` is the **campaign-level orchestrator**. Give it a campaign brief and it handles everything: budget allocation across channels, parallel inventory research, human approval, and booking execution.

Use this when you have a campaign budget and want the system to figure out what to buy.

```python
from ad_buyer.flows.deal_booking_flow import DealBookingFlow
from ad_buyer.clients.opendirect_client import OpenDirectClient
from ad_buyer.storage.deal_store import DealStore

# Setup
client = OpenDirectClient(base_url="http://seller.example.com:8001")
store = DealStore("sqlite:///./deals.db")
store.connect()

flow = DealBookingFlow(client=client, store=store)

# Define your campaign
flow.state.campaign_brief = {
    "name": "Q3 Awareness Push",
    "objectives": ["brand_awareness"],
    "budget": 25000,
    "start_date": "2026-07-01",
    "end_date": "2026-09-30",
    "target_audience": {
        "demographics": {"age": "18-34"},
        "interests": ["gaming", "technology"],
    },
    "kpis": {"target_cpm": 10},
}

# Run — this kicks off the full pipeline
result = flow.kickoff()
```

**What happens inside:**

1. **Brief validation** -- Checks required fields (objectives, budget, dates, audience).
2. **Audience planning** -- Analyzes targeting requirements, estimates coverage per channel.
3. **Budget allocation** -- Portfolio manager splits budget across channels (branding, CTV, mobile, performance).
4. **Parallel research** -- Channel specialists research inventory simultaneously.
5. **Consolidation** -- All recommendations are gathered for review.
6. **Approval checkpoint** -- Waits for human approval (or auto-approves).
7. **Booking execution** -- Books approved recommendations.

**Approving recommendations:**

After the flow reaches `awaiting_approval` status:

```python
# Check what's pending
status = flow.get_status()
print(f"Status: {status['execution_status']}")
print(f"Pending: {status['pending_approvals']} recommendations")

# Approve specific products
result = flow.approve_recommendations(["prod_001", "prod_003"])

# Or approve everything
result = flow.approve_all()
print(f"Booked {result['booked']} lines, total cost: ${result['total_cost']}")
```

**Via the REST API:**

```bash
# Start a booking
curl -X POST http://localhost:8001/bookings \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "name": "Q3 Awareness Push",
      "objectives": ["brand_awareness"],
      "budget": 25000,
      "start_date": "2026-07-01",
      "end_date": "2026-09-30",
      "target_audience": {"demographics": {"age": "18-34"}}
    }
  }'

# Poll for status
curl http://localhost:8001/bookings/{job_id}

# Approve all recommendations
curl -X POST http://localhost:8001/bookings/{job_id}/approve-all
```

---

## Using DSPDealFlow (Single-Deal, Direct Mode)

`DSPDealFlow` is for when you know roughly what you want and just need a Deal ID. It discovers inventory, picks the best match, and requests a deal -- all in one shot.

Use this for **single-deal, targeted acquisition** rather than full-campaign planning.

```python
from ad_buyer.flows.dsp_deal_flow import run_dsp_deal_flow
from ad_buyer.models.buyer_identity import BuyerIdentity, DealType
from ad_buyer.storage.deal_store import DealStore

store = DealStore("sqlite:///./deals.db")
store.connect()

result = await run_dsp_deal_flow(
    request="Premium sports video inventory for Q3 awareness campaign",
    buyer_identity=BuyerIdentity(
        seat_id="ttd-seat-123",
        agency_id="omnicom-456",
    ),
    deal_type=DealType.PREFERRED_DEAL,
    impressions=500_000,
    max_cpm=30.0,
    flight_start="2026-07-01",
    flight_end="2026-09-30",
    store=store,
)

print(f"Status: {result['status']['status']}")
print(f"Deal: {result['status']['deal_response']}")
```

**Flow steps:**

1. **Receive request** -- Validates the natural-language request and buyer context.
2. **Discover inventory** -- Searches the seller's catalog for matches.
3. **Evaluate and select** -- Uses a DSP agent (CrewAI) to pick the best product.
4. **Request Deal ID** -- Calls the seller's deal endpoint for the selected product.

**Key difference from DealBookingFlow:**

| | DealBookingFlow | DSPDealFlow |
|---|---|---|
| **Scope** | Full campaign, multiple channels | Single deal |
| **Input** | Campaign brief with budget | Natural language request |
| **Selection** | Multi-channel specialists | Single DSP agent |
| **Approval** | Human checkpoint | Automatic |
| **Output** | Multiple booked lines | One Deal ID |

---

## Handling Errors and Retries

### DealsClient Errors

The `DealsClient` raises `DealsClientError` for all API and transport failures:

```python
from ad_buyer.clients.deals_client import DealsClient, DealsClientError

try:
    quote = await client.request_quote(quote_request)
except DealsClientError as e:
    print(f"Error: {e}")
    print(f"HTTP status: {e.status_code}")   # 0 for transport errors
    print(f"Error code: {e.error_code}")     # Machine-readable (e.g. "timeout")
    print(f"Detail: {e.detail}")             # Human-readable message
```

**Built-in retry logic:**

The client automatically retries on **transient server errors** (502, 503, 504) and **timeouts**, up to 3 attempts by default:

```python
# Customize retry and timeout behavior
client = DealsClient(
    seller_url="http://seller.example.com:8001",
    api_key="your-api-key",
    timeout=60.0,       # 60 seconds (default: 30)
    max_retries=5,      # 5 attempts (default: 3)
)
```

**What gets retried vs. what doesn't:**

| Error | Retried? |
|-------|----------|
| 502 Bad Gateway | Yes |
| 503 Service Unavailable | Yes |
| 504 Gateway Timeout | Yes |
| Request timeout | Yes |
| 400 Bad Request | No |
| 401 Unauthorized | No |
| 404 Not Found | No |
| 409 Conflict | No |
| Connection refused | No |

### Expired Quotes

Quotes have a limited lifetime. If you try to book an expired quote, you'll get a 409 or 400 error. The fix is simple -- request a new quote:

```python
try:
    deal = await client.book_deal(DealBookingRequest(quote_id=old_quote_id))
except DealsClientError as e:
    if e.error_code == "quote_expired":
        # Re-quote and try again
        new_quote = await client.request_quote(original_request)
        deal = await client.book_deal(DealBookingRequest(quote_id=new_quote.quote_id))
```

### DealStore Persistence Errors

The `DealsClient` and flow classes treat persistence as **best-effort**. If the `DealStore` fails (disk full, locked, etc.), the API call still succeeds -- you just lose the local record. Errors are logged but never re-raised.

---

## Tips and Best Practices

**Start with the media kit.** Browse before you quote. The media kit shows you what's available, at what price range, and whether negotiation is enabled. Going straight to `request_quote` with a random product ID will likely fail.

**Reveal your identity for better pricing.** Pass `buyer_identity` on both the quote request and the booking request. The seller uses this to calculate tier-based discounts. Going from PUBLIC to ADVERTISER tier can save 15% on CPM.

**Use the DealStore.** Attach a `DealStore` to your `DealsClient` for automatic persistence. It costs nothing if the DB is healthy, and gives you a complete audit trail of every quote, deal, and status change.

**Check `negotiation_enabled` before negotiating.** Not all packages support negotiation, and not all tiers can negotiate. Attempting to negotiate when it's not available wastes time and may return errors.

**Book promptly.** Quotes expire. If you're running a multi-step workflow (browse, quote, negotiate, book), don't let the quote sit too long. The seller may have reserved inventory capacity that times out.

**Use `DealBookingFlow` for campaigns, `DSPDealFlow` for spot buys.** If you have a campaign brief with a budget to allocate across channels, use `DealBookingFlow`. If you just need a single Deal ID for a specific product, use `DSPDealFlow` or the `DealsClient` directly.

**Always use async context managers.** The `DealsClient` holds an HTTP connection pool. Use `async with` to ensure clean shutdown:

```python
async with DealsClient(seller_url=url, api_key=key) as client:
    # ... do your work ...
# Connection pool is closed automatically
```

---

## Related

- [Negotiation](negotiation.md) -- Multi-turn price negotiation strategies
- [Media Kit](../api/media-kit.md) -- Browsing seller inventory
- [Bookings API](../api/bookings.md) -- REST API for the booking workflow
- [Authentication](../api/authentication.md) -- API key setup and access tiers
- [Seller Agent Integration](../integration/seller-agent.md) -- Connecting to a seller
