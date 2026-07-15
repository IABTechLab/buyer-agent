# AudiencePlan Wire-Format Specification

**Status:** Active  
**Bead:** ar-g7nf  
**Proposal refs:** §5.1, §5.6, §5.7, §6 rows 14b/15, §7  

This document is the authoritative wire-format definition for `AudiencePlan` over HTTP.
Code in both repos cites it by section number; **do not renumber sections**.

---

## §1 Purpose and Scope

`AudiencePlan` is the buyer-emitted audience targeting payload threaded through deal
booking. It is produced by the Audience Planner agent and attached to
`DealBookingRequest` (buyer side) / accepted by `POST /api/v1/deals` (seller side).
The plan carries one primary audience plus optional constraint, extension, and exclusion
audiences composed from three ref types (standard, contextual, agentic).

This document specifies:

- The JSON schema for `AudiencePlan` and `AudienceRef` (§2).
- The HTTP content-type negotiation contract (§3).
- The OpenRTB v2.6 translation for impression-time targeting (§4).
- The booking-time lifecycle and forensic logging (§5).
- A stable field-reference table (§6) cited by code comments.
- The seller rejection / buyer fallback protocol (§7).
- Versioning strategy (§8).
- Migration policy for the OpenRTB agentic extension (§9).

---

## §2 JSON Schema

### §2.1 AudiencePlan

Top-level object carried in `DealBookingRequest.audience_plan`.

```json
{
  "schema_version": "1",
  "audience_plan_id": "sha256:<hex>",
  "primary": { ... },
  "constraints": [ ... ],
  "extensions": [ ... ],
  "exclusions": [ ... ],
  "rationale": "Human-readable planner explanation"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | `string` | no | Schema version; currently `"1"`. Bumped on breaking changes. Default `"1"`. |
| `audience_plan_id` | `string` | no | `sha256:<hex>` content hash (see §2.3). Auto-computed when blank. |
| `primary` | `AudienceRef` | **yes** | The primary audience for the campaign. |
| `constraints` | `AudienceRef[]` | no | Refs that intersect with primary (precision). Default `[]`. |
| `extensions` | `AudienceRef[]` | no | Refs that union with primary (reach). Default `[]`. |
| `exclusions` | `AudienceRef[]` | no | Refs subtracted from the assembled audience. Default `[]`. |
| `rationale` | `string` | no | Human-readable explanation; excluded from the hash. Default `""`. |

See `src/ad_buyer/models/audience_plan.py` (`AudiencePlan`).

### §2.2 AudienceRef

A single audience reference. The `type` field is the discriminator.

```json
{
  "type": "standard",
  "identifier": "3-7",
  "taxonomy": "iab-audience",
  "version": "1.1",
  "source": "explicit",
  "confidence": null,
  "compliance_context": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `"standard" \| "contextual" \| "agentic"` | **yes** | Ref type; discriminates `identifier` semantics. |
| `identifier` | `string` | **yes** | IAB Audience Taxonomy ID (`standard`), IAB Content Taxonomy ID (`contextual`), or embedding URI (`agentic`, e.g. `emb://…`). |
| `taxonomy` | `string` | **yes** | `"iab-audience"` \| `"iab-content"` \| `"agentic-audiences"` |
| `version` | `string` | **yes** | Taxonomy version, e.g. `"1.1"`, `"3.1"`, `"draft-2026-01"`. |
| `source` | `"explicit" \| "resolved" \| "inferred"` | **yes** | Provenance of the ref. |
| `confidence` | `float \| null` | no | Match confidence in `[0, 1]`. MUST be `null` when `source="explicit"`. |
| `compliance_context` | `ComplianceContext \| null` | conditional | **Required** when `type="agentic"`; optional otherwise. |

See `src/ad_buyer/models/audience_plan.py` (`AudienceRef`) and the mirrored
`src/ad_seller/models/audience_ref.py`.

### §2.2.1 ComplianceContext

Consent regime accompanying an `AudienceRef`. Required for `type=agentic`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `jurisdiction` | `string` | **yes** | Jurisdiction code: `"US"`, `"EU"`, `"GLOBAL"`. |
| `consent_framework` | `string` | **yes** | `"IAB-TCFv2"`, `"GPP"`, `"advertiser-1p"`, `"none"`. |
| `consent_string_ref` | `string \| null` | no | Opaque pointer to the consent string (not the raw string). |
| `attestation` | `string \| null` | no | Hash or signature for required attestation. |
| `embedding_provenance` | `string \| null` | no | `"local_buyer"`, `"advertiser_supplied"`, `"hosted_external"`, or `"mock"`. |

Note: agentic refs with `jurisdiction="GLOBAL"` are **rejected at brief ingestion**
(see `validate_no_global_agentic` in `src/ad_buyer/models/audience_plan.py`). A single
`ComplianceContext` cannot honestly span multiple consent regimes. Until per-jurisdiction
fan-out lands (proposal §7), separate refs per jurisdiction are required.

### §2.3 compute_id() Determinism

`AudiencePlan.audience_plan_id` is the SHA-256 hash of the plan's canonical content:

1. Extract roles `{primary, constraints, extensions, exclusions}` via
   `model_dump(mode="json")`.
2. Recursively sort all dict keys (canonical form); list order is preserved —
   planner role-ordering is semantically significant.
3. Serialize with `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
   and encode UTF-8.
4. Compute `hashlib.sha256(payload).hexdigest()` and prefix with `"sha256:"`.

Fields excluded from the hash: `audience_plan_id` itself, `schema_version`,
`rationale`.

Result is stable across Pydantic field-order changes. It is **not** stable across
reorderings of list items within a role.

See `src/ad_buyer/models/audience_plan.py:compute_id()`.

---

## §3 Content-Type Negotiation

Two media types are accepted on `audience_plan`-bearing requests (see
`src/ad_buyer/clients/deals_client.py:63-65` and
`src/ad_seller/interfaces/api/main.py:39-41`):

| Name | Media Type |
|------|-----------|
| Legacy UCP carrier | `application/vnd.ucp.embedding+json; v=1` |
| IAB Agentic Audiences | `application/vnd.iab.agentic-audiences+json; v=1` |

**Buyer emission (booking-time):**

- `Content-Type: application/vnd.ucp.embedding+json; v=1` — the legacy UCP carrier
  remains the emit name during the transition window for backward compatibility with
  sellers that predate the rename (proposal §5.6 lock #1).
- `Accept: application/vnd.ucp.embedding+json; v=1, application/vnd.iab.agentic-audiences+json; v=1` —
  advertises that the buyer can read either name on the seller's response.

**Bookings without `audience_plan`** keep the default `Content-Type: application/json`
headers; this path is unchanged.

**Seller acceptance:**

Both names parse via the same JSON body. FastAPI's JSON body parser reads the body
regardless of `Content-Type`, so both are accepted without custom middleware. Servers
MAY echo the request's `Content-Type` on the response. See
`src/ad_seller/interfaces/api/main.py:38-41`.

---

## §4 OpenRTB v2.6 Translation

After a deal is booked, the buyer translates the `AudiencePlan` into OpenRTB v2.6
fragments for impression-time bid requests. The builder
(`src/ad_buyer/clients/openrtb_builder.py`) returns a `dict` with `"user"` and/or
`"site"` keys that the campaign runtime merges into a full `BidRequest`.

| Ref type | OpenRTB carrier | Notes |
|----------|-----------------|-------|
| `standard` | `user.data[].segment[]` | All standard refs grouped under a single `user.data` entry with `name="IAB_Taxonomy"` and `ext.taxonomy_version` set to the first ref's version. |
| `contextual` | `site.cat[]` + `site.cattax=7` | `cattax=7` is the OpenRTB 2.6 enum for IAB Content Taxonomy 3.1. Contextual exclusions are dropped (OpenRTB `site.cat` has no exclusion semantics) with a structured `logger.warning`. |
| `agentic` | `user.ext.iab_agentic_audiences.refs[]` | Feature-flagged; emitted only when `enable_agentic_openrtb_ext=True` (default off). When disabled, agentic refs are dropped with a structured warning. See §9. |

### §4.1 Standard Ref Mapping

```json
{
  "user": {
    "data": [
      {
        "name": "IAB_Taxonomy",
        "ext": {"taxonomy_version": "1.1"},
        "segment": [
          {"id": "3-7"},
          {"id": "3-8", "value": "0.85"},
          {"id": "3-9", "ext": {"exclude": true}}
        ]
      }
    ]
  }
}
```

Exclusion segments carry `ext.exclude=true` (ad-hoc; sellers MAY honor).
Confidence values from resolved/inferred refs surface as `"value"` (string per
OpenRTB segment schema).

### §4.2 Contextual Ref Mapping

```json
{
  "site": {
    "cat": ["IAB1-2", "IAB3-7"],
    "cattax": 7
  }
}
```

### §4.3 Agentic Ref Mapping (feature-flagged)

When enabled (`enable_agentic_openrtb_ext=True` in buyer settings):

```json
{
  "user": {
    "ext": {
      "iab_agentic_audiences": {
        "refs": [
          {
            "identifier": "emb://buyer.example.com/audiences/sports-fans-v3",
            "version": "draft-2026-01",
            "source": "explicit",
            "compliance_context": {
              "jurisdiction": "US",
              "consent_framework": "GPP",
              "consent_string_ref": null,
              "attestation": null,
              "embedding_provenance": "local_buyer"
            }
          }
        ]
      }
    }
  }
}
```

The namespaced key `iab_agentic_audiences` is temporary pending IAB ratification.
See §9 for the 90-day dual-emit migration policy.

See `src/ad_buyer/clients/openrtb_builder.py`.

---

## §5 Lifecycle

```
Buyer                                     Seller
  |                                           |
  |--- POST /api/v1/deals ------------------>|
  |    Content-Type: vnd.ucp.embedding+json  |
  |    body: { ..., "audience_plan": {...} } |
  |                                           |
  |                          validate plan   |
  |                       against capabilities|
  |                                           |
  |              (if unsupported)             |
  |<-- 400 audience_plan_unsupported --------|
  |    (buyer degrades plan and retries)      |
  |                                           |
  |              (if supported)               |
  |                          freeze snapshot  |
  |                          compute match    |
  |                          log plan_id hash |
  |<-- 200 DealResponse ---------------------|
  |    audience_plan_snapshot                 |
  |    audience_match_summary                 |
  |                                           |
  | log plan_id hash                          |
```

Both sides log the `audience_plan_id` hash at booking time (INFO level):

- Buyer: `ad_buyer.audience.booking` logger, message:
  `deal_booking audience_plan_id=<hash> quote_id=<id>`
  (see `src/ad_buyer/clients/deals_client.py:234-238`)
- Seller: `ad_seller.audience.booking` logger, message:
  `deal_booking deal_id=<id> audience_plan_id=<hash> quote_id=<id>`
  (see `src/ad_seller/interfaces/api/main.py:2513-2518`)

Matching log entries across both systems are the forensic anchor for post-booking
dispute resolution about what was frozen at booking time.

---

## §6 Field Reference Table

Rows are cited by number in code comments. Do not reorder or renumber.

| Row | Surface | Field / Behavior | Source |
|-----|---------|-----------------|--------|
| 1 | `AudiencePlan` | `schema_version` | `models/audience_plan.py` |
| 2 | `AudiencePlan` | `audience_plan_id` (sha256-prefixed hash) | `models/audience_plan.py:compute_id()` |
| 3 | `AudiencePlan` | `primary` (required `AudienceRef`) | `models/audience_plan.py` |
| 4 | `AudiencePlan` | `constraints[]` | `models/audience_plan.py` |
| 5 | `AudiencePlan` | `extensions[]` | `models/audience_plan.py` |
| 6 | `AudiencePlan` | `exclusions[]` | `models/audience_plan.py` |
| 7 | `AudiencePlan` | `rationale` (excluded from hash) | `models/audience_plan.py` |
| 8 | `AudienceRef` | `type` discriminator (`standard`/`contextual`/`agentic`) | `models/audience_plan.py` |
| 9 | `AudienceRef` | `identifier` (taxonomy ID or embedding URI) | `models/audience_plan.py` |
| 10 | `AudienceRef` | `compliance_context` (required on `type=agentic`) | `models/audience_plan.py` |
| 11 | Seller validation | Per-role capability gating + cardinality cap | `services/audience_plan_validator.py` |
| 12 | Content-Type | `application/vnd.ucp.embedding+json; v=1` (emit name) | `clients/deals_client.py:63` |
| 13 | Content-Type | `application/vnd.iab.agentic-audiences+json; v=1` (alias) | `clients/deals_client.py:64` |
| 14b | Forensic logging | Buyer + seller both log `audience_plan_id` hash at booking (INFO) | `clients/deals_client.py:234`, `interfaces/api/main.py:2513` |
| 15 | OpenRTB agentic ext | `enable_agentic_openrtb_ext` feature flag (default off) | `clients/openrtb_builder.py:99` |

### §6.5 audience_match_summary

The seller's deal booking response includes `audience_match_summary` when an
`audience_plan` was present in the request. Shape:

```json
{
  "audience_match_summary": {
    "primary": {"match": "STRONG", "score": 0.91},
    "constraints": [{"match": "MODERATE", "score": 0.72}],
    "extensions": [],
    "exclusions": []
  }
}
```

Match bucket labels: `STRONG` (≥ 0.85), `MODERATE` (≥ 0.65), `WEAK` (≥ 0.40),
`NONE` (< 0.40). Empty arrays MAY be omitted by the server but receivers MUST treat
absence as empty (the buyer's `AudienceMatchSummary` model defaults all roles to `[]`).

See `src/ad_seller/interfaces/api/main.py:_build_audience_match_summary()` and
`src/ad_buyer/models/deals.py:AudienceMatchSummary`.

---

## §7 Rejection and Fallback

If the seller receives an `audience_plan` with any part it cannot honor, it returns
HTTP 400 with a structured `audience_plan_unsupported` body (proposal §5.7 layer 3):

```json
{
  "detail": {
    "error": "audience_plan_unsupported",
    "unsupported": [
      {"path": "extensions[0]", "reason": "extensions not supported by this seller"},
      {"path": "primary.taxonomy", "reason": "version '3.2' not supported (seller supports ['1.1', '3.1'])"}
    ]
  }
}
```

The `unsupported` list contains `{"path": str, "reason": str}` entries for every
unsupported element. The path syntax mirrors the plan's JSON structure
(`primary.taxonomy`, `constraints[0].taxonomy`, `extensions`, `exclusions`).

**Buyer behavior:** `DealsClient._build_error_from_response()` parses the FastAPI-
wrapped `detail` shape and surfaces the `unsupported` list on
`DealsClientError.unsupported`. The buyer's orchestrator calls
`degrade_plan_for_seller()` against the precise drops the seller listed, then retries.
See `src/ad_buyer/clients/deals_client.py:78` (`DealsClientError.unsupported`).

**Seller validation logic:** `src/ad_seller/services/audience_plan_validator.py`
validates taxonomy versions, per-role gating, and cardinality caps against the seller's
`CapabilityAudienceBlock`.

---

## §8 Versioning

The `v=1` parameter in both media types signals schema version 1. The `schema_version`
field in the JSON body echoes this (`"1"` currently).

Forward-compatibility strategy:

- The seller MUST ignore unknown fields in `AudiencePlan` and `AudienceRef` (Pydantic's
  default `extra="ignore"` on the seller's dict-based acceptance).
- Unknown `AudienceRef.type` values are reported as unsupported via the structured
  rejection in §7 (forward-compat: the seller doesn't know how to honor them).
- When a breaking schema change is needed, increment `v=` in the media type to `v=2`
  and bump `schema_version`. Both sides MUST continue accepting `v=1` during the
  transition window (length TBD per IAB Tech Lab process).

---

## §9 Migration Policy: OpenRTB Agentic Extension

The `user.ext.iab_agentic_audiences` key (§4.3) is a temporary namespaced extension
pending IAB ratification of a standard OpenRTB agentic audience slot.

**90-day dual-emit policy:** Once IAB ratifies an extension key, the buyer will:

1. Emit the ratified key in addition to `iab_agentic_audiences` for 90 days
   (dual-emit window).
2. At day 90, drop `iab_agentic_audiences` and emit only the ratified key.

Sellers SHOULD accept both keys during the dual-emit window and prefer the ratified key.

The dual-emit is gated by the same `enable_agentic_openrtb_ext` flag (row 15 above)
so deployments that do not want to ship either key during the transition can hold the
flag off. See `src/ad_buyer/clients/openrtb_builder.py:6-28` for the current policy
comment.

---

## Related

- [Deals API Client](deals.md) — `DealsClient` usage and quote-then-book flow
- [Bookings API](bookings.md) — Campaign booking workflow (brief-based)
- `src/ad_buyer/models/audience_plan.py` — Buyer-side model and `compute_id()`
- `src/ad_buyer/clients/deals_client.py` — Content-type emission and forensic logging
- `src/ad_buyer/clients/openrtb_builder.py` — OpenRTB translation
- `src/ad_seller/interfaces/api/main.py` — Seller acceptance, validation, and logging
- `src/ad_seller/services/audience_plan_validator.py` — Structured rejection logic
- `src/ad_seller/models/audience_ref.py` — Seller-side mirror of `AudienceRef`
- `docs/proposals/AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md` — Upstream proposal
