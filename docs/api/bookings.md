# Bookings API

The bookings endpoints manage the full campaign booking lifecycle -- from brief submission through approval to deal execution.

## Status Lifecycle

```
pending --> running --> awaiting_approval --> completed
                                         \-> failed
                   \-> failed
```

| Status | Meaning |
|--------|---------|
| `pending` | Job created, background flow starting |
| `running` | Budget allocation and inventory research in progress |
| `awaiting_approval` | Recommendations ready for human review |
| `completed` | Deals booked (or no recommendations approved) |
| `failed` | An error occurred during the flow |

---

## POST /bookings

Start a new booking workflow. The flow runs in the background; poll `GET /bookings/{job_id}` for progress.

### Request Body -- `BookingRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `brief` | `CampaignBrief` | yes | Campaign details (see below) |
| `auto_approve` | `bool` | no | Automatically approve all recommendations. Default: `false` |

#### CampaignBrief

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` (1-100 chars) | yes | Campaign name |
| `objectives` | `list[string]` (min 1) | yes | Campaign objectives (e.g. `brand_awareness`, `reach`, `conversions`) |
| `budget` | `float` (> 0) | yes | Total campaign budget |
| `start_date` | `string` (YYYY-MM-DD) | yes | Campaign start date |
| `end_date` | `string` (YYYY-MM-DD) | yes | Campaign end date |
| `target_audience` | `object` | yes | Audience targeting specification |
| `kpis` | `object` | no | Key performance indicators |
| `channels` | `list[string]` | no | Preferred channels (e.g. `branding`, `ctv`, `mobile_app`, `performance`) |

### Response -- `BookingResponse`

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `string` | Unique job identifier (UUID) |
| `status` | `string` | Initial status: `pending` |
| `message` | `string` | Human-readable next-step message |

### Example

```bash
curl -X POST http://localhost:8001/bookings \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "name": "Q3 Awareness Push",
      "objectives": ["brand_awareness"],
      "budget": 25000,
      "start_date": "2026-07-01",
      "end_date": "2026-09-30",
      "target_audience": {
        "demographics": {"age": "18-34"},
        "interests": ["gaming", "technology"]
      },
      "kpis": {"target_cpm": 10}
    },
    "auto_approve": false
  }'
```

---

## GET /bookings/{job_id}

Retrieve the current status of a booking workflow.

### Response -- `BookingStatus`

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `string` | Job identifier |
| `status` | `string` | Current status (see lifecycle above) |
| `progress` | `float` | Progress from 0.0 to 1.0 |
| `budget_allocations` | `object \| null` | Channel budget splits |
| `recommendations` | `list[object] \| null` | Product recommendations pending approval |
| `booked_lines` | `list[object] \| null` | Confirmed booked line items |
| `errors` | `list[string] \| null` | Error messages, if any |
| `created_at` | `string` | ISO 8601 creation timestamp |
| `updated_at` | `string` | ISO 8601 last-update timestamp |

### Example

```bash
curl http://localhost:8001/bookings/a1b2c3d4-5678-90ab-cdef-1234567890ab
```

---

## POST /bookings/{job_id}/approve

Approve specific product recommendations for booking. Only valid when the job status is `awaiting_approval`.

### Request Body -- `ApprovalRequest`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `approved_product_ids` | `list[string]` | yes | Product IDs to approve |

### Response

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `success` or `failed` |
| `approved_count` | `int` | Number of products approved |
| `booked` | `int` | Number of line items booked |
| `total_cost` | `float` | Total cost of booked items |

### Example

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve \
  -H "Content-Type: application/json" \
  -d '{"approved_product_ids": ["prod_001", "prod_003"]}'
```

---

## POST /bookings/{job_id}/approve-all

Approve all pending recommendations for booking. Only valid when the job status is `awaiting_approval`.

### Response

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | `success` or `failed` |
| `booked` | `int` | Number of line items booked |
| `total_impressions` | `int` | Total impressions across booked lines |
| `total_cost` | `float` | Total cost of booked items |

### Example

```bash
curl -X POST http://localhost:8001/bookings/a1b2c3d4-.../approve-all
```

---

## GET /bookings

List all booking jobs, optionally filtered by status.

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status` | `string` | (none) | Filter by status (e.g. `awaiting_approval`, `completed`) |
| `limit` | `int` | 20 | Maximum number of results |

### Response

| Field | Type | Description |
|-------|------|-------------|
| `jobs` | `list[object]` | Job summaries (job_id, status, campaign_name, budget, created_at) |
| `total` | `int` | Total number of matching jobs |

### Example

```bash
curl "http://localhost:8001/bookings?status=awaiting_approval&limit=5"
```

---

## Error Handling

All booking endpoints return structured error responses with an HTTP status code and a JSON body containing `error` and `detail` fields.

```json
{
  "error": "not_found",
  "detail": "Booking job a1b2c3d4-... not found"
}
```

### Error Codes

| Endpoint | HTTP Status | `error` | When |
|----------|------------|---------|------|
| `POST /bookings` | `400` | `invalid_brief` | Required brief fields missing or invalid (e.g., budget <= 0, empty objectives, end_date before start_date) |
| `POST /bookings` | `422` | `validation_error` | Brief payload fails Pydantic schema validation |
| `GET /bookings/{job_id}` | `404` | `not_found` | No booking job exists with the given ID |
| `POST /bookings/{job_id}/approve` | `404` | `not_found` | No booking job exists with the given ID |
| `POST /bookings/{job_id}/approve` | `409` | `invalid_status` | Job is not in `awaiting_approval` status |
| `POST /bookings/{job_id}/approve` | `400` | `invalid_products` | None of the submitted product IDs match pending recommendations |
| `POST /bookings/{job_id}/approve-all` | `404` | `not_found` | No booking job exists with the given ID |
| `POST /bookings/{job_id}/approve-all` | `409` | `invalid_status` | Job is not in `awaiting_approval` status |
| `GET /bookings` | `422` | `validation_error` | Invalid query parameter value (e.g., non-integer `limit`) |
| *any* | `500` | `internal_error` | Unexpected server error during booking flow execution |

### Booking Flow Failures

When the background booking flow fails (seller unreachable, budget allocation error, etc.), the job transitions to `failed` status rather than returning an HTTP error. Poll `GET /bookings/{job_id}` and check the `errors` array:

```json
{
  "job_id": "a1b2c3d4-...",
  "status": "failed",
  "errors": [
    "Seller agent unreachable at http://seller:8001",
    "Budget allocation failed: no valid channel splits found"
  ]
}
```
