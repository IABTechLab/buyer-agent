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

### Domain matching

SGP is not required to echo back the exact spelling that was queried — it may answer with the vendor's canonical (apex) domain for a queried subdomain, or the reverse. The client therefore pairs each returned record back to the domain that was actually requested:

1. Exact match on the normalized domain.
2. Otherwise a parent/child match on a label boundary — so an `example.com` record resolves a queried `news.example.com`, while `notexample.com` is never matched by an `example.com` record.

Matching is deliberately conservative, and there is no third step: a record for an unrelated domain is **never** accepted as the verdict for a queried one, not even when it is the only record in the response. Attributing another vendor's approval would make the gate fail open, and the deal-request stage always queries exactly one domain — precisely the shape where a permissive fallback does the most damage.

When more than one record legitimately matches, the **most specific** (longest) matching domain wins, so a verdict never depends on the order records happen to appear in the response. Among equally specific records, a non-approval wins — a response carrying contradictory records can never upgrade a vendor to approved.

A record that cannot be paired with any requested domain is logged at `WARNING` and ignored rather than silently dropped.

This matters for caching: whatever the client resolves is what gets cached for `SGP_CACHE_TTL_SECONDS`. An unresolved approval would otherwise report the vendor as UNKNOWN and suppress that verdict for the full TTL.

### Transient failures and retries

Because enforcement fails closed, a failed lookup and a denial have the same effect on a deal — and at the orchestrator stage a single failed lookup excludes *every* seller. A momentary blip should not carry that weight, so the client retries before giving up.

| Aspect | Behavior |
|---|---|
| Retried | Transport errors (connect, timeout, DNS, read) and HTTP `429`, `502`, `503`, `504` |
| Never retried | `200`, `400`, `401`, `404` — these are answers, not blips, and retrying only delays the verdict |
| Attempts | `1 + max_retries`; `max_retries` defaults to `2`, so three attempts |
| Backoff | Exponential — `retry_backoff_seconds * 2**attempt`, base `0.5s` by default, so `0.5s` then `1.0s` |
| On exhaustion | The original failure is raised as `SGPClientError`, so enforcing callers still fail closed |

`max_retries` and `retry_backoff_seconds` are `SGPClient` constructor arguments rather than environment variables; the defaults suit the enforcing paths, and `max_retries=0` disables retrying entirely. Each retry is logged at `WARNING` with the reason and the attempt number, and the final error names the attempt count — so a one-off `503` and a sustained outage read differently in logs.

Retries do not change any verdict. They only affect how long the gate waits before concluding it cannot verify a vendor.

!!! warning "Retries multiply the worst-case wait"
    The backoff itself is small, but each retry is a fresh request that can burn the full `timeout`. With the defaults (15s timeout, three attempts), a chunk that keeps timing out takes up to roughly **46s** — three timeouts plus 1.5s of backoff — where it previously took 15s. Chunks of 10 domains are fetched **sequentially**, so a lookup spanning several chunks multiplies that again: 30 distinct seller domains is three chunks, hence up to ~140s in the pathological case.

    This only bites when SGP is timing out; a reachable SGP that answers or refuses does so on the first attempt. If your deployment sits behind a tighter deadline, lower `max_retries`, lower the client `timeout`, or both.

## Configuration

| Variable | Type | Default | Description                                                                                                                                                          |
|----------|------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `SGP_API_KEY` | `str` | `""` | API key from the SGP api. Empty = integration disabled.                                                                                                              |
| `SGP_BASE_URL` | `str` | `https://api.safeguardprivacy.com` | Production endpoint. The staging environment is `https://api.safeguardprivacy-demo.com`.                                                                             |
| `SGP_ENFORCE` | `bool` | `False` | When `True`, NOT APPROVED vendors are filtered out at discovery, the deal-request gate blocks Deal ID generation, and SGP transport errors halt the flow.            |
| `SGP_UNKNOWN_VENDOR_POLICY` | `str` | `"block"` | Behavior for domains not in the SGP portfolio (HTTP 404). One of `block`, `warn`, `allow` — matched case-insensitively, so `BLOCK` and `Block` are accepted. An unrecognized value fails at settings load rather than being interpreted per call site. Applies at discovery, the deal-request stage, and the orchestrator gate when enforcement is on. |
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

`DiscoverInventoryTool` accepts an optional `SGPClient`. When provided, it extracts the seller domain from each returned product, batches distinct domains into groups of 10, and annotates each product row in the formatted output.

Domain extraction probes, in order: `domain` (the field the OpenDirect Product resource defines), `seller_domain` / `sellerDomain`, then the deal / SSP-connector names `publisherDomain`, `publisher_domain`, `seller_url` — and finally `publisherId` / `publisher` if they contain a `.`. Products should populate `domain`; the connector names are accepted only so a deal dict derived from an SSP connector also resolves.

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
| Transport error (after retries) | ❌ flow halts | ❌ flow halts | ❌ flow halts |
| Product has no seller domain field | ❌ filtered at discovery; blocked at request | ❌ | ❌ |

The last row is the one to watch when enabling enforcement against a live catalog: a product the gate cannot resolve a domain for is blocked regardless of unknown-vendor policy, and SGP is never called. Populate the OpenDirect Product `domain` field — see [Domain matching](#domain-matching) for the full probe order.

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
| `Deal blocked: IAB Diligence Platform lookup failed` | SGP stayed unreachable across every attempt (the client already retried — see [Transient failures and retries](#transient-failures-and-retries)). Enforcement fails closed; retry once the service is reachable. The message and the preceding `WARNING` lines carry the attempt count and the underlying failure. |
| `sgp.vendor_gate` events with outcome `check_failed` for every seller | One SGP lookup failed after retries, so the orchestrator failed closed for the whole discovery batch rather than booking unverified vendors. Check the event `reason` for the exception class and detail. |
| `ValidationError` for `sgp_unknown_vendor_policy` at startup | `SGP_UNKNOWN_VENDOR_POLICY` is set to something outside `block` / `warn` / `allow`. Casing is not the problem (it is normalized); a typo is. |
| Gate seems to do nothing | `SGP_ENFORCE=false` (the default) — the gate is fully inert. With `SGP_ENFORCE=true` and no key, the pipeline fails closed instead (no sellers pass discovery); check the logs and `sgp.vendor_gate` events. |
| `Deal blocked: cannot determine seller domain` / discovery reports `N missing seller domain` | The product carries none of the domain fields the gate probes, so it is blocked without SGP being called. Populate the Product `domain` field — see [Domain matching](#domain-matching). |
| Log: `SGP returned an approval record for <domain> that could not be paired with any requested domain` | SGP answered about a domain unrelated to any queried one. The record is ignored and the queried domain stays UNKNOWN. Confirm the vendor's domain in SGP matches the seller domain on the product. |

## Related

- [Configuration reference](../guides/configuration.md) — all env vars including SGP
- [Seller Agent Integration](seller-agent.md) — the seller side of the deal request
