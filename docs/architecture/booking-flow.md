# Booking Flow

The `DealBookingFlow` is a CrewAI event-driven flow that orchestrates the end-to-end booking process. It is defined in `flows/deal_booking_flow.py` and extends `Flow[BookingState]`.

## Sequence Diagram

```mermaid
sequenceDiagram
    actor User as User / Campaign Manager
    participant API as Buyer API<br/>(FastAPI)
    participant Flow as DealBookingFlow<br/>(CrewAI)
    participant Portfolio as Portfolio Manager<br/>Agent
    participant Channels as Channel Specialist<br/>Agents
    participant ODClient as OpenDirect<br/>Client
    participant Seller as Seller Agent<br/>API

    User->>API: POST /bookings (CampaignBrief)
    API->>API: Create job (status: pending)
    API-->>User: 202 {job_id, status: pending}
    API->>Flow: Background task: kickoff()

    Note over Flow: Step 1: Validate brief
    Flow->>Flow: receive_campaign_brief()
    Flow->>Flow: plan_audience()

    Note over Flow: Step 2: Budget allocation
    Flow->>Portfolio: allocate_budget()
    Portfolio-->>Flow: Channel allocations (branding, ctv, mobile, performance)

    Note over Flow: Step 3: Parallel inventory research
    par Branding research
        Flow->>Channels: research_branding()
        Channels->>ODClient: search products, check avails
        ODClient->>Seller: GET /products, POST /products/avails
        Seller-->>ODClient: Product list, availability
        ODClient-->>Channels: Products + pricing
        Channels-->>Flow: Branding recommendations
    and CTV research
        Flow->>Channels: research_ctv()
        Channels->>ODClient: search products, check avails
        ODClient->>Seller: GET /products, POST /products/avails
        Seller-->>ODClient: Product list, availability
        ODClient-->>Channels: Products + pricing
        Channels-->>Flow: CTV recommendations
    and Performance research
        Flow->>Channels: research_performance()
        Channels-->>Flow: Performance recommendations
    and Mobile research
        Flow->>Channels: research_mobile()
        Channels-->>Flow: Mobile recommendations
    end

    Note over Flow: Step 4: Consolidate
    Flow->>Flow: consolidate_recommendations()
    Flow->>Flow: Set status: awaiting_approval

    User->>API: GET /bookings/{job_id}
    API-->>User: {status: awaiting_approval, recommendations: [...]}

    alt auto_approve = true
        Flow->>Flow: approve_all()
        Flow->>Flow: _execute_bookings()
        Flow->>Flow: Set status: completed
    else Manual approval
        User->>API: POST /bookings/{job_id}/approve<br/>{approved_product_ids: [...]}
        API->>Flow: approve_recommendations(ids)
        Flow->>Flow: _execute_bookings()
        Flow-->>API: {status: success, booked: N}
        API-->>User: {status: success, booked: N, total_cost: X}
    end

    User->>API: GET /bookings/{job_id}
    API-->>User: {status: completed, booked_lines: [...]}
```

## Flow Steps

| Step | Method | Trigger | Description |
|------|--------|---------|-------------|
| 1 | `receive_campaign_brief()` | `@start()` | Validates required fields and budget |
| 2 | `plan_audience()` | Listens to step 1 | Builds audience plan, estimates coverage, identifies gaps |
| 3 | `allocate_budget()` | Listens to step 2 | Portfolio crew splits budget across channels |
| 4a | `research_branding()` | Listens to step 3 | Branding crew searches display/video inventory |
| 4b | `research_ctv()` | Listens to step 3 | CTV crew searches streaming inventory |
| 4c | `research_mobile()` | Listens to step 3 | Mobile crew searches app inventory |
| 4d | `research_performance()` | Listens to step 3 | Performance crew searches remarketing inventory |
| 5 | `consolidate_recommendations()` | Listens to 4a-4d (OR) | Waits for all active channels, flattens recommendations |
| 6 | `approve_recommendations()` / `approve_all()` | External call (API) | Marks recommendations as approved/rejected |
| 7 | `_execute_bookings()` | Called by step 6 | Creates BookedLine entries for approved items |

## Execution Status Transitions

```mermaid
stateDiagram-v2
    [*] --> initialized
    initialized --> brief_received: receive_campaign_brief
    initialized --> validation_failed: invalid brief
    brief_received --> budget_allocated: allocate_budget
    budget_allocated --> researching: research_* agents
    researching --> awaiting_approval: consolidate_recommendations
    awaiting_approval --> executing_bookings: approve
    executing_bookings --> completed: bookings done
    budget_allocated --> failed: error
    researching --> failed: error
    executing_bookings --> failed: error
```

These states are tracked in `BookingState.execution_status` using the `ExecutionStatus` enum.
