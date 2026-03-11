# Running with Seller

Walk through a full booking workflow with the buyer and seller agents running together.

## Prerequisites

- Buyer agent installed and running (see [Quickstart](quickstart.md))
- Seller agent installed and running — follow the [Seller Agent Quickstart](https://iabtechlab.github.io/seller-agent/getting-started/quickstart/)

!!! tip "Default Ports"
    The seller agent runs on port **3000** by default, the buyer agent on port **8001**. The buyer's `OPENDIRECT_BASE_URL` should point to the seller's API (e.g. `http://localhost:3000/api/v2.1`).

## Start Both Agents

### 1. Start the seller agent

In a separate terminal:

```bash
cd seller_agent
uvicorn seller_agent.api.main:app --reload --port 3000
```

Verify the seller is running:

```bash
curl http://localhost:3000/health
```

### 2. Start the buyer agent

In another terminal:

```bash
cd ad_buyer_system
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

Verify the buyer is running:

```bash
curl http://localhost:8001/health
```

## Booking Workflow

### 1. Create a booking

Submit a campaign brief to the buyer agent. It will contact the seller agent in the background to find matching inventory, allocate budget, and build recommendations.

```bash
curl -X POST http://localhost:8001/bookings \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "name": "Summer Campaign 2026",
      "objectives": ["brand_awareness", "reach"],
      "budget": 50000,
      "start_date": "2026-07-01",
      "end_date": "2026-08-31",
      "target_audience": {
        "demographics": {"age": "25-54"},
        "interests": ["travel", "outdoor"]
      },
      "kpis": {"target_cpm": 12, "viewability": 70},
      "channels": ["branding", "ctv"]
    },
    "auto_approve": false
  }'
```

Response:

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "pending",
  "message": "Booking workflow started. Use GET /bookings/{job_id} to check status."
}
```

### 2. Check status

Poll the booking endpoint until the status reaches `awaiting_approval` (or `completed` if `auto_approve` was true):

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

### 3. Approve recommendations

Once the booking reaches `awaiting_approval`, review the recommendations and approve them.

Approve all recommendations:

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve-all
```

Or approve specific products:

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve \
  -H "Content-Type: application/json" \
  -d '{"approved_product_ids": ["prod_001", "prod_003"]}'
```

### 4. View results

```bash
curl http://localhost:8001/bookings/a1b2c3d4-...
```

The response now includes `booked_lines` with confirmed deal details from the seller.

## Next Steps

- [**Deal Booking Guide**](../guides/deal-booking.md) — Detailed explanation of the booking lifecycle and deal states.
- [**Negotiation**](../guides/negotiation.md) — How the buyer agent negotiates pricing and terms.
- [**Media Kit Browsing**](../guides/media-kit.md) — Explore seller inventory before booking.
- [**Multi-Seller Discovery**](../guides/multi-seller.md) — Connect to multiple sellers simultaneously.
- [**Seller Agent Integration**](../integration/seller-agent.md) — Technical details on the buyer-seller protocol.
