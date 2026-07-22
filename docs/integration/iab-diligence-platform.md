# IAB Diligence Platform

The buyer agent can verify, before issuing a Deal ID, that the buyer has explicitly approved a seller's vendor record for IAB buyer-agent purchases. Approvals are stored in the buyer's [IAB Diligence Platform](https://safeguardprivacy.com/iab-diligence-platform/) tenant; the buyer agent consults them through SGP's integration API.

This integration is **optional and off by default**. When `SGP_API_KEY` is empty the feature is fully inert — the buyer agent behaves exactly as it did before this page existed. Once configured, it acts as a privacy rail in front of the existing deal workflow.

## Who should enable this

IAB Diligence Platform customers who treat vendor onboarding and approval as a compliance prerequisite for programmatic buying. If your team already maintains a vendor inventory in SGP with IAB buyer-agent approval flags, this integration enforces that workflow inside the buyer agent itself.

## Endpoint contract

The client calls a single endpoint on the IAB Diligence Platform (SafeGuard Privacy API):

```
GET /api/v1/integrations/iab/buyer-agent-approval?domain=a.com,b.com
```

| Property     | Value                                                   |
|--------------|---------------------------------------------------------|
| Auth         | `api-key` header                                        |
| Domain       | `domain` query parameter - Up to 10 domains per request |
| Tenant scope | Results are scoped to the caller's SGP tenant           |

The response contains one `IabBuyerAgentResource` per matched vendor:

```json
{
  "status": "success",
  "code": 200,
  "data": [
{
      "vendorId": 123,
      "vendorCompanyId": 456,
      "companyName": "Example Publisher",
      "domain": "example.com",
      "iabBuyerAgentApproval": true,
      "iabBuyerAgentApprovedAt": "2026-03-14T12:00:00Z"
    }
  ]
}
```

Three response states matter to the buyer agent:

| State | Meaning | How the gate treats it |
|-------|---------|------------------------|
| `iabBuyerAgentApproval: true` | Buyer has approved this vendor | ✅ Deal proceeds |
| `iabBuyerAgentApproval: false` | Vendor exists but is not approved | ❌ Deal blocked |
| HTTP 404 | Vendor is not in the buyer's SGP portfolio | Governed by `SGP_UNKNOWN_VENDOR_POLICY` |

## Configuration

| Variable | Type | Default | Description                                                                                                                                                          |
|----------|------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `SGP_API_KEY` | `str` | `""` | API key from the SGP api. Empty = integration disabled.                                                                                                              |
| `SGP_BASE_URL` | `str` | `https://api.safeguardprivacy.com` | Production endpoint. The staging environment is `https://api.safeguardprivacy-demo.com`.                                                                             |
| `SGP_ENFORCE` | `bool` | `False` | When `True`, NOT APPROVED vendors are filtered out at discovery, the deal-request gate blocks Deal ID generation, and SGP transport errors halt the flow.            |
| `SGP_UNKNOWN_VENDOR_POLICY` | `str` | `"block"` | Behavior for domains not in the SGP portfolio (HTTP 404). One of `block`, `warn`, `allow`. Applies at both discovery and deal-request stages when enforcement is on. |
| `SGP_CACHE_TTL_SECONDS` | `int` | `900` | Per-domain cache lifetime. Discovery→pricing→booking reuse a single SGP call within the TTL.                                                                         |

!!! warning "Enforcement without a key fails closed"
    If `SGP_ENFORCE=true` but `SGP_API_KEY` is empty, the canonical booking pipeline cannot verify any vendor and **fails closed**: no seller passes discovery until a key is configured. The buyer agent logs an error at orchestrator construction time, and each excluded seller gets an `sgp.vendor_gate` event with outcome `unconfigured` and a causeful reason. Enforcement never silently books unverified vendors because a key is missing.

## Where the gate runs

### Canonical booking pipeline

The gate is wired into the real booking path: `DealBookingFlow` → `MultiSellerOrchestrator`. When `SGP_ENFORCE=true`, the orchestrator's discovery stage batches every discovered seller's domain into a single approval lookup (the client chunks by 10 and caches per `SGP_CACHE_TTL_SECONDS`) and excludes sellers that fail the check **before any quote or booking request is sent**. Each per-seller decision is emitted on the event bus as `sgp.vendor_gate` with an outcome:

| Outcome | Meaning | Seller kept? |
|---|---|---|
| `approved` | SGP verifies the vendor's IAB buyer-agent approval | ✅ |
| `denied` | Vendor exists in SGP but is NOT approved | ❌ |
| `unknown_blocked` / `unknown_warned` / `unknown_allowed` | Vendor not in the SGP portfolio; per `SGP_UNKNOWN_VENDOR_POLICY` | per policy |
| `no_domain` | No domain derivable from the seller URL — unverifiable | ❌ |
| `check_failed` | The SGP lookup itself failed — **all** sellers fail closed | ❌ |
| `unconfigured` | Enforcing with no `SGP_API_KEY` — **all** sellers fail closed | ❌ |

Every excluding outcome carries a non-empty, causeful `reason` (for transport failures: exception class plus detail). With `SGP_ENFORCE=false` (the default) the pipeline makes **zero** SGP calls and behaves exactly as before.

### Example tools

The integration also plugs into two example buyer-agent tools. Behavior at each stage is governed by the same `SGP_ENFORCE` flag.

### Inventory discovery

`DiscoverInventoryTool` accepts an optional `SGPClient`. When provided, it extracts the seller domain from each returned product (checking `seller_url`, `publisher_domain`, then `publisherId`/`publisher` if they contain a `.`), batches distinct domains into groups of 10, and annotates each product row in the formatted output:

```
1. Premium CTV - Sports
   Product ID: ctv-premium-sports
   Publisher: premium-pub-001
   CPM: $28.26 (was $35.00)
   SGP Approval: ✓ APPROVED — seller.example.com
```

Behavior depends on `SGP_ENFORCE`:

| `SGP_ENFORCE` | NOT APPROVED rows | Unknown vendors | Missing seller domain | SGP transport error |
|---|---|---|---|---|
| `false` (annotate only) | kept + annotated | kept + annotated | kept (no annotation) | logged, no annotations |
| `true` (filter) | **filtered out** | governed by `SGP_UNKNOWN_VENDOR_POLICY` | filtered out | flow halts (fails closed) |

When enforcement removes any products, a tail line is appended so the action is auditable:

```
--------------------------------------------------
Total products found: 4
SGP enforcement filtered 2 product(s): 1 not approved, 1 unknown to SGP
```

### Deal-request gate

`RequestDealTool` checks the seller's vendor approval after fetching product details and before generating a Deal ID. The gate acts as a safety net behind discovery filtering — it runs only when an `SGPClient` is wired in and `sgp_enforce=True`:

```python
# Construct the tool with SGP wiring from settings
# (see examples/dsp_deal_discovery.py for a complete workflow)
RequestDealTool(
    client=unified_client,
    buyer_context=ctx,
    sgp_client=sgp_client,
    sgp_enforce=settings.sgp_enforce,
    sgp_unknown_policy=settings.sgp_unknown_vendor_policy,
)
```

A successful gate prepends a banner to the Deal ID response:

```
SGP: ✓ Example Publisher approved for IAB buyer-agent purchases (since 2026-03-14T12:00:00Z).

============================================================
DEAL CREATED SUCCESSFULLY
============================================================
...
```

A failed gate returns a blocking message and does **not** generate a Deal ID.

## Behavior matrix

With enforcement on (`SGP_ENFORCE=true`, `SGP_API_KEY` set), behavior is consistent across stages:

| SGP response | `block` policy | `warn` policy | `allow` policy |
|---|---|---|---|
| `iabBuyerAgentApproval: true` | ✅ kept + approved banner | same | same |
| `iabBuyerAgentApproval: false` | ❌ filtered at discovery; blocked at request | ❌ | ❌ |
| 404 (not onboarded in SGP) | ❌ filtered at discovery; blocked at request | ✅ kept + warning annotation/banner | ✅ kept silently |
| Transport error | ❌ flow halts | ❌ flow halts | ❌ flow halts |
| Product has no seller domain field | ❌ filtered at discovery; blocked at request | ❌ | ❌ |

The `iabBuyerAgentApproval: false` row is intentionally the same across all three unknown-vendor policies — an explicit non-approval is always fatal. The policies only govern the "unknown to SGP" case.

## Agent tool

For CrewAI agents that want to consult approvals outside the automatic gate, a tool is provided:

```python
from ad_buyer.clients import SGPClient
from ad_buyer.tools.research import SGPVendorApprovalTool

sgp = SGPClient(api_key=settings.sgp_api_key, base_url=settings.sgp_base_url)
tool = SGPVendorApprovalTool(client=sgp)

# Agent calls it with a list of domains (any number; client chunks to 10)
# Returns a formatted APPROVED / NOT APPROVED / UNKNOWN summary.
```

Give this tool to an agent alongside the discovery and deal-request tools so it can consult approval status during product selection (before commitment), not only at Deal ID generation time.

!!! note "Canonical flow is gated automatically"
    The canonical `DealBookingFlow` / `MultiSellerOrchestrator` pipeline constructs the gate from settings on its own (see "Canonical booking pipeline" above) — no manual wiring needed there. The `DiscoverInventoryTool` / `RequestDealTool` wiring shown above applies to custom workflows built on the example tools, exercised by `examples/dsp_deal_discovery.py`.

The class is prefixed `SGP` so future vendor-approval integrations can coexist under their own class names and CrewAI `name` attributes without colliding.

## Troubleshooting

| Symptom | Likely cause                                                                                                                                   |
|---------|------------------------------------------------------------------------------------------------------------------------------------------------|
| `IAB Diligence Platform rejected the api-key` (401) | The key is missing, revoked, or lacks the proper scope. Request a new key from SGP.                                                            |
| `Deal blocked: <domain> is not in your IAB Diligence Platform portfolio` | The vendor is not onboarded in SGP. Add and approve the vendor in SGP, or switch `SGP_UNKNOWN_VENDOR_POLICY` to `warn` for soft-fail behavior. |
| `Deal blocked: <vendor> does not carry the IAB buyer-agent approval flag` | The vendor is onboarded but not marked approved for IAB buyer-agent purchases. Toggle the approval in SGP.                                     |
| `Deal blocked: IAB Diligence Platform lookup failed` | SGP was unreachable or returned a transient error. Enforcement fails closed; retry once the service is reachable.                              |
| Gate seems to do nothing | `SGP_ENFORCE=false` (the default) — the gate is fully inert. With `SGP_ENFORCE=true` and no key, the pipeline fails closed instead (no sellers pass discovery); check the logs and `sgp.vendor_gate` events. |

## Related

- [Configuration reference](../guides/configuration.md) — all env vars including SGP
- [Seller Agent Integration](seller-agent.md) — the seller side of the deal request
