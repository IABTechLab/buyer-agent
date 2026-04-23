# IAB Buyer-Agent Approval (via SafeGuard Privacy)

The buyer agent can verify, before issuing a Deal ID, that the buyer has explicitly approved a seller's vendor record for IAB buyer-agent purchases. Approvals are stored in the buyer's [SafeGuard Privacy](https://safeguardprivacy.com) tenant; the buyer agent consults them through SGP's integration API.

This integration is **optional and off by default**. When `SGP_API_KEY` is empty the feature is fully inert — the buyer agent behaves exactly as it did before this page existed. Once configured, it acts as a privacy rail in front of the existing deal workflow.

## Who should enable this

SafeGuard Privacy customers who treat vendor onboarding and approval as a compliance prerequisite for programmatic buying. If your team already maintains a vendor inventory in SGP with IAB buyer-agent approval flags, this integration enforces that workflow inside the buyer agent itself.

## Endpoint contract

The client calls a single endpoint on the SafeGuard Privacy platform:

```
GET /api/v1/integrations/iab/buyer-agent-approval?domain=a.com,b.com
```

| Property | Value |
|----------|-------|
| Auth | `api-key` header |
| Scope | `iab:buyerAgent` |
| Batch size | Up to 10 domains per request |
| Tenant scope | Results are scoped to the caller's SGP `companyId` |

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
      "internalId": "",
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

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SGP_API_KEY` | `str` | `""` | API key with the `iab:buyerAgent` scope. Empty = integration disabled. |
| `SGP_BASE_URL` | `str` | `https://api.safeguardprivacy.com` | Production endpoint. The staging environment is `https://api.safeguardprivacy-demo.com`. |
| `SGP_ENFORCE_ON_DEAL_REQUEST` | `bool` | `False` | When `True`, `RequestDealTool` blocks Deal ID generation unless the seller's vendor is approved. |
| `SGP_UNKNOWN_VENDOR_POLICY` | `str` | `"block"` | Behavior for domains not in the SGP portfolio (HTTP 404). One of `block`, `warn`, `allow`. |
| `SGP_CACHE_TTL_SECONDS` | `int` | `900` | Per-domain cache lifetime. Discovery→pricing→booking reuse a single SGP call within the TTL. |

!!! warning "Enforcement without a key is a no-op"
    If `SGP_ENFORCE_ON_DEAL_REQUEST=true` but `SGP_API_KEY` is empty, the gate cannot be evaluated and is silently bypassed. The buyer agent logs a warning at flow construction time so this misconfiguration is visible.

## Where the gate runs

The integration plugs into two existing buyer-agent tools:

### Inventory discovery annotations

`DiscoverInventoryTool` accepts an optional `SGPClient`. When provided, it extracts the seller domain from each returned product (checking `seller_url`, `publisher_domain`, then `publisherId`/`publisher` if they contain a `.`), batches distinct domains into groups of 10, and annotates each product row in the formatted output:

```
1. Premium CTV - Sports
   Product ID: ctv-premium-sports
   Publisher: premium-pub-001
   CPM: $28.26 (was $35.00)
   SGP Approval: ✓ APPROVED — seller.example.com
```

Discovery **fails open** on SGP transport errors — the tool logs and continues without annotations, so a SafeGuard outage never breaks inventory browsing. The actual enforcement is always at the deal-request step.

### Deal-request gate

`RequestDealTool` checks the seller's vendor approval after fetching product details and before generating a Deal ID. The gate runs only when an `SGPClient` is wired in and `sgp_enforce=True`:

```python
# Injected automatically by BuyerDealFlow from settings
RequestDealTool(
    client=unified_client,
    buyer_context=ctx,
    sgp_client=sgp_client,
    sgp_enforce=settings.sgp_enforce_on_deal_request,
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

With enforcement on (`SGP_ENFORCE_ON_DEAL_REQUEST=true`, `SGP_API_KEY` set):

| SGP response | `block` policy | `warn` policy | `allow` policy |
|---|---|---|---|
| `iabBuyerAgentApproval: true` | ✅ deal proceeds + banner | same | same |
| `iabBuyerAgentApproval: false` | ❌ blocked | ❌ blocked | ❌ blocked |
| 404 (not onboarded in SGP) | ❌ blocked | ✅ proceeds + warning banner | ✅ proceeds silently |
| Transport error | ❌ fails closed | ❌ fails closed | ❌ fails closed |
| Product has no seller domain field | ❌ blocked (cannot evaluate) | ❌ | ❌ |

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

`BuyerDealFlow` injects this tool into the Buyer Deal Specialist automatically when an SGP client is configured, so the agent can consult approval status during product selection (before commitment), not only at Deal ID generation time.

The class is prefixed `SGP` so future vendor-approval integrations can coexist under their own class names and CrewAI `name` attributes without colliding.

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| `SafeGuard Privacy rejected the api-key` (401) | The key is missing, revoked, or lacks the `iab:buyerAgent` scope. Issue a new key in SGP with that scope. |
| `Deal blocked: <domain> is not in your SafeGuard Privacy portfolio` | The vendor is not onboarded in SGP. Add and approve the vendor in SGP, or switch `SGP_UNKNOWN_VENDOR_POLICY` to `warn` for soft-fail behavior. |
| `Deal blocked: <vendor> does not carry the IAB buyer-agent approval flag` | The vendor is onboarded but not marked approved for IAB buyer-agent purchases. Toggle the approval in SGP. |
| `Deal blocked: SafeGuard Privacy lookup failed` | SGP was unreachable or returned a transient error. Enforcement fails closed; retry once the service is reachable. |
| Gate seems to do nothing | Either `SGP_API_KEY` is empty or `SGP_ENFORCE_ON_DEAL_REQUEST=false`. Check startup logs for the bypass warning. |

## Related

- [Configuration reference](../guides/configuration.md) — all env vars including SGP
- [Buyer Deal Flow](../architecture/buyer-deal-flow.md) — the flow the gate plugs into
- [Seller Agent Integration](seller-agent.md) — the seller side of the deal request
