# SSP Integration Plan: Deal Library Import Connectors

**Date:** 2026-03-25
**Bead:** buyer-xa5 (SSP Integration Planning — PubMatic, Magnite, Index Exchange)
**Status:** Planning complete; implementation beads created

---

## 1. Context and Scope

The buyer agent deal library already supports two methods for populating deals: CSV file upload (`import_deals_csv` MCP tool) and manual entry (`create_deal_manual` MCP tool). This plan adds a third class of import methods: **SSP connectors** that pull deals directly from SSP APIs into the deal library.

### What SSP connectors are (and are not)

**They are:** Inbound deal sync mechanisms. The SSP connector connects to an SSP's buyer-facing API, retrieves deals that have been created and shared with the buyer, normalizes the data, and saves deals to the `DealStore` — the same storage used by CSV import.

**They are not:** Deal creation tools. Deal creation in programmatic advertising is fundamentally seller-initiated (see `docs/planning/DEAL_BOOKING_RESEARCH_2026-03-25.md` in agent_range). SSPs do not accept deal creation requests from buyers. The connector pulls; it does not push.

### Deal flow direction

```
SSP (PubMatic / Magnite / Index Exchange)
  |
  | Deal Sync API (SSP-to-buyer direction)
  | API credentials held by buyer
  v
SSP Connector (fetches deals, normalizes to DealStore schema)
  |
  | Identical to what CSV import already does
  v
DealStore (SQLite-backed, same schema as CSV/manual imports)
  |
  v
MCP tools expose imported deals to AI assistants (list_deals, inspect_deal, etc.)
```

This matches how real-world DSPs receive deals from SSPs (Deal ID inbox, deal sync APIs, IAB Deals API v1.0). The connector is the buyer agent's equivalent of a DSP's deal sync receiver.

---

## 2. Architecture

### 2.1 Where connectors live

SSP connectors are a new module under `src/ad_buyer/tools/deal_library/`:

```
src/ad_buyer/
  tools/
    deal_library/
      __init__.py
      deal_entry.py          # existing -- manual entry
      portfolio_inspection.py # existing
      templates.py           # existing
      ssp_connector_base.py  # NEW -- abstract base class (buyer-ssp-base)
      connectors/
        __init__.py
        pubmatic.py          # NEW -- PubMatic connector (buyer-ssp-pm)
        magnite.py           # NEW -- Magnite connector (buyer-ssp-mg)
        index_exchange.py    # NEW -- Index Exchange connector (buyer-ssp-ix)
  interfaces/
    mcp_server.py            # UPDATED -- new SSP import MCP tools (buyer-ssp-mcp)
```

### 2.2 Relationship to existing import pattern

The CSV import pattern (`deal_import.py` + `import_deals_csv` MCP tool) defines the integration contract for SSP connectors:

1. **Parse/fetch** deals from source (CSV file → SSP API)
2. **Normalize** to `DealStore.save_deal()` keyword arguments
3. **Persist** via `store.save_deal(**deal_data)`
4. **Record metadata** via `store.save_portfolio_metadata(deal_id=..., import_source="PUBMATIC", ...)`

SSP connectors replace steps 1 and 2 with API calls instead of CSV parsing. Steps 3 and 4 are identical.

The `import_source` field in `portfolio_metadata` distinguishes SSP-imported deals:
- CSV import: `import_source="CSV"`
- PubMatic: `import_source="PUBMATIC"`
- Magnite: `import_source="MAGNITE"`
- Index Exchange: `import_source="INDEX_EXCHANGE"`

---

## 3. Common Connector Interface

All three SSP connectors implement a shared abstract base class defined in `ssp_connector_base.py`.

### 3.1 SSPConnector abstract base class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class SSPFetchResult:
    """Result of an SSP deal fetch operation."""
    deals: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_fetched: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    ssp_name: str = ""
    raw_response_count: int = 0  # number of deals returned by SSP before filtering


class SSPConnector(ABC):
    """Abstract base class for all SSP deal import connectors.

    Subclasses implement fetch_deals() and _normalize_deal() for their
    specific SSP API format. The base class handles:
    - SSPFetchResult construction
    - Deduplication (by seller_deal_id)
    - Error collection
    - import_source tagging

    Usage:
        connector = PubMaticConnector(api_token="...", seat_id="...")
        result = connector.fetch_deals(status="active")
        for deal in result.deals:
            store.save_deal(**deal)
            store.save_portfolio_metadata(
                deal_id=saved_id,
                import_source=connector.import_source,
                import_date=today,
            )
    """

    @property
    @abstractmethod
    def ssp_name(self) -> str:
        """Human-readable SSP name (e.g. 'PubMatic')."""
        ...

    @property
    @abstractmethod
    def import_source(self) -> str:
        """Import source tag for portfolio metadata (e.g. 'PUBMATIC')."""
        ...

    @abstractmethod
    def fetch_deals(self, **kwargs) -> SSPFetchResult:
        """Fetch deals from the SSP and return normalized DealStore dicts.

        kwargs are connector-specific filter parameters (status, date range,
        deal type, etc.). The returned deals are ready for DealStore.save_deal().
        """
        ...

    @abstractmethod
    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        """Normalize a single raw SSP API deal to DealStore kwargs.

        Returns a dict matching DealStore.save_deal() keyword arguments.
        Must set at minimum: seller_url, product_id, deal_type, status,
        seller_deal_id, display_name, seller_org, seller_type="SSP".
        """
        ...
```

### 3.2 DealStore fields populated by SSP connectors

All connectors map SSP API response fields to these `DealStore.save_deal()` kwargs:

| DealStore Field | SSP Source | Notes |
|-----------------|------------|-------|
| `seller_url` | SSP API base URL | Set to SSP's API endpoint URL |
| `product_id` | SSP deal ID | Reuse `seller_deal_id` or SSP-specific package ID |
| `deal_type` | SSP deal type field | Normalize to: PG, PD, PA, OPEN_AUCTION |
| `status` | SSP status | Map to: `imported`, `active`, `paused` |
| `seller_deal_id` | SSP deal ID | The deal ID that appears in OpenRTB bid requests |
| `display_name` | SSP deal name | |
| `seller_org` | SSP name | "PubMatic", "Magnite", "Index Exchange" |
| `seller_domain` | Publisher domain | If provided by SSP |
| `seller_type` | Hardcoded | Always `"SSP"` for SSP connectors |
| `bid_floor_cpm` | Floor price | |
| `fixed_price_cpm` | Fixed price (PG/PD) | |
| `currency` | SSP currency field | Default `"USD"` |
| `media_type` | SSP media/channel | Normalize to: DIGITAL, CTV, LINEAR_TV, AUDIO, DOOH |
| `formats` | SSP creative formats | JSON string |
| `geo_targets` | SSP geo targeting | JSON string |
| `content_categories` | SSP content cats | JSON string |
| `audience_segments` | SSP audience data | JSON string (PubMatic Targeted PMP) |
| `flight_start` | Deal start date | ISO 8601 |
| `flight_end` | Deal end date | ISO 8601 |
| `impressions` | Guaranteed volume | Integer (PG deals) |
| `description` | SSP notes field | |

---

## 4. PubMatic Connector (Priority 1)

PubMatic is the first connector because it is the most buyer-friendly SSP: it exposes direct buyer-facing PMP APIs, supports "Targeted PMP" (buyer-configurable deals), and is the only major SSP to ship an open-source MCP server (`github.com/PubMatic/pubmatic-mcp-server`). PubMatic's MCP server uses the same protocol as the buyer agent's own MCP interface, making it directly relevant to the architecture.

### 4.1 API details

- **Base URL:** `https://api.pubmatic.com`
- **Key endpoints:**
  - `GET /pmp/deals` — list PMP deals for buyer seat
  - `GET /v3/pmp/deals/{deal_id}` — fetch single deal detail
  - `GET /pmp/deals?status=active` — filter by status
- **Authentication:** API Access Token (bearer token, per-seat)
  - Token generated through PubMatic platform (requires PubMatic account)
  - Header: `Authorization: Bearer <token>`
- **Deal types returned:** PMP (Private Marketplace), PG (Programmatic Guaranteed), PD (Preferred Deal)
- **Targeted PMP:** PubMatic's "Audience Encore" feature lets buyers create audience-targeted PMP deals; these appear in the same deal list endpoint with audience segment fields populated

### 4.2 PubMatic MCP server reference

PubMatic's open-source MCP server (`github.com/PubMatic/pubmatic-mcp-server`) provides two agents:
- **Deal Creation & Copy Agent** — creates/clones PMP, PG, and Preferred deals (seller/publisher side)
- **Deal Troubleshooting Agent** — diagnoses deal performance issues

The buyer agent's PubMatic connector does **not** use PubMatic's MCP server directly (their server is for publisher/seller use). Instead, the connector calls PubMatic's REST API (`api.pubmatic.com`) directly. PubMatic's MCP server is referenced here as architectural documentation of what PubMatic's AI integration model looks like — it confirms that PubMatic has invested in programmatic AI-native interfaces and their deal data model is well-structured for machine access.

### 4.3 Data mapping (PubMatic API response → DealStore)

PubMatic API response fields map to DealStore as follows:

| PubMatic Field | DealStore Field | Notes |
|----------------|-----------------|-------|
| `deal_id` | `seller_deal_id` | The OpenRTB deal ID |
| `name` | `display_name` | |
| `status` | `status` | Map: `active`→`active`, `inactive`→`paused`, `pending`→`imported` |
| `deal_type` | `deal_type` | Map: `PMP`→`PA`, `PG`→`PG`, `preferred`→`PD` |
| `floor_price` | `bid_floor_cpm` | |
| `fixed_cpm` | `fixed_price_cpm` | PG/PD deals |
| `currency` | `currency` | |
| `publisher_domain` | `seller_domain` | |
| `format` | `formats` | JSON string |
| `geo` | `geo_targets` | JSON string |
| `categories` | `content_categories` | |
| `audience_segments` | `audience_segments` | Targeted PMP only |
| `start_date` | `flight_start` | ISO 8601 |
| `end_date` | `flight_end` | |
| `impressions` | `impressions` | PG guaranteed volume |
| `notes` | `description` | |
| (hardcoded) | `seller_org` | `"PubMatic"` |
| (hardcoded) | `seller_type` | `"SSP"` |
| (hardcoded) | `seller_url` | `"https://api.pubmatic.com"` |

### 4.4 Connector configuration

```python
class PubMaticConnector(SSPConnector):
    """PubMatic SSP deal import connector.

    Credentials:
        api_token: PubMatic API Access Token (contact PubMatic representative)
        seat_id: PubMatic buyer seat ID

    Fetch filters (all optional):
        status: "active" | "inactive" | "pending" (default: all)
        deal_type: "PMP" | "PG" | "preferred" (default: all)
        page: int (default 1)
        page_size: int (default 100, max 500)
    """
```

---

## 5. Magnite Connector (Priority 2)

Magnite is the #1 CTV SSP (Roku 26%, Fire TV 17%) and #2 US web SSP (11%). Despite its market dominance, Magnite does **not** expose a direct buyer-facing API for deal management. Buyers receive Magnite deals through their DSP's Deal ID inbox — which auto-syncs hourly. The buyer agent models this via a **pull-based connector** that queries the Magnite streaming SSP API using DSP credentials.

### 5.1 Platform split

Magnite operates two distinct platforms with separate API endpoints:

| Platform | Formerly | Inventory | API Base |
|----------|----------|-----------|----------|
| Magnite DV+ | Rubicon Project | Display, Video | `api.rubiconproject.com` |
| Magnite Streaming | SpotX | CTV/OTT | `api.tremorhub.com` |

The connector must handle both platforms. In practice, for a reference implementation, the Streaming (CTV) API is higher priority given Magnite's CTV dominance.

### 5.2 API details (Magnite Streaming / CTV)

- **Base URL:** `https://api.tremorhub.com`
- **Key endpoints:**
  - `GET /v1/resources/seats/{seatId}/deals` — list deals for seat
  - `GET /v1/resources/seats/{seatId}/deals/{dealId}` — single deal
- **Authentication:** Session-based auth using access-key/secret-key
  - `POST /v1/resources/login` with credentials → session cookie
  - Subsequent requests use session cookie
- **Deal types:** Deals returned are seller-created; buyer can accept/reject
- **Deal sync:** Magnite deals auto-refresh hourly in DSP inboxes; the connector pulls the current state

### 5.3 Key limitation

Magnite's API is seller-side. The buyer seat can **read** deals that have been targeted at their seat. This is the correct model (SSP-to-buyer import), but it means:
- The `seatId` in API calls is the buyer's DSP seat ID as registered with Magnite
- Credentials are typically provisioned by Magnite's partner success team
- No buyer-initiated deal creation is possible

### 5.4 Data mapping (Magnite API response → DealStore)

| Magnite Field | DealStore Field | Notes |
|---------------|-----------------|-------|
| `dealId` | `seller_deal_id` | |
| `name` | `display_name` | |
| `status` | `status` | Map: `active`→`active`, `inactive`→`paused` |
| `type` | `deal_type` | Map: `private_auction`→`PA`, `preferred`→`PD`, `programmatic_guaranteed`→`PG` |
| `floorCpm` | `bid_floor_cpm` | |
| `fixedCpm` | `fixed_price_cpm` | PG/PD |
| `currency` | `currency` | |
| `publisherDomain` | `seller_domain` | |
| `mediaType` | `media_type` | Map: `video`→`CTV`, `display`→`DIGITAL` |
| `startDate` | `flight_start` | |
| `endDate` | `flight_end` | |
| `impressions` | `impressions` | PG only |
| (hardcoded) | `seller_org` | `"Magnite"` (or `"Magnite Streaming"` for CTV) |
| (hardcoded) | `seller_type` | `"SSP"` |
| (hardcoded) | `seller_url` | `"https://api.tremorhub.com"` |

### 5.5 Connector configuration

```python
class MagniteConnector(SSPConnector):
    """Magnite SSP deal import connector.

    Supports both Magnite Streaming (CTV/OTT) and Magnite DV+ (display/video).
    Default platform is Streaming (higher priority for CTV deal coverage).

    Credentials:
        access_key: Magnite access key
        secret_key: Magnite secret key
        seat_id: Buyer seat ID registered with Magnite
        platform: "streaming" (default) | "dv_plus"
    """
```

---

## 6. Index Exchange Connector (Priority 3)

Index Exchange is the #1 US web SSP (19% share). Its deal model is strictly publisher-side: publishers create deals via IX's UI or API, specifying buyer seats. The buyer agent's IX connector discovers and imports deals that have been created by publishers and targeted to the buyer's seat.

### 6.1 API details

- **Base URL:** `https://api.indexexchange.com`
- **Authentication:** API key (login required via IX API portal to obtain)
- **Key endpoints:**
  - `GET /deals` — list deals targeted to the buyer's seat
  - `GET /deals/{deal_id}` — single deal detail
- **Note:** The "Create deal" endpoint in IX's API docs is publisher-side. The buyer agent uses the GET endpoints only.
- **Deal activation model:** IX uses "easy deal activation" — buyers can discover and activate deals created by publishers without manually entering deal IDs

### 6.2 Data mapping (Index Exchange API response → DealStore)

| IX Field | DealStore Field | Notes |
|----------|-----------------|-------|
| `dealId` | `seller_deal_id` | |
| `name` | `display_name` | |
| `status` | `status` | |
| `dealType` | `deal_type` | Map: `PMP`→`PA`, `PG`→`PG`, `PD`→`PD` |
| `floorPrice` | `bid_floor_cpm` | |
| `price` | `fixed_price_cpm` | PG/PD |
| `currency` | `currency` | |
| `publisherDomain` | `seller_domain` | |
| `adType` | `media_type` | Map: `video`→`CTV`, `display`→`DIGITAL` |
| `startDate` | `flight_start` | |
| `endDate` | `flight_end` | |
| `impressions` | `impressions` | |
| `description` | `description` | |
| (hardcoded) | `seller_org` | `"Index Exchange"` |
| (hardcoded) | `seller_type` | `"SSP"` |
| (hardcoded) | `seller_url` | `"https://api.indexexchange.com"` |

### 6.3 Connector configuration

```python
class IndexExchangeConnector(SSPConnector):
    """Index Exchange SSP deal import connector.

    Deal creation is publisher-side only; this connector discovers and
    imports deals that publishers have targeted to the buyer's seat.

    Credentials:
        api_key: Index Exchange API key
        seat_id: Buyer seat/member ID
    """
```

---

## 7. MCP Tools

The SSP connectors are exposed to AI assistants through new MCP tools added to `src/ad_buyer/interfaces/mcp_server.py`.

### 7.1 New MCP tools

| Tool Name | Description |
|-----------|-------------|
| `list_ssp_connectors` | List available SSP connectors and their configuration status (configured / unconfigured) |
| `import_deals_pubmatic` | Fetch deals from PubMatic API and import to deal library |
| `import_deals_magnite` | Fetch deals from Magnite API and import to deal library |
| `import_deals_index_exchange` | Fetch deals from Index Exchange API and import to deal library |

### 7.2 Tool signatures

**`list_ssp_connectors()`**
Returns a JSON list of available connectors, each with:
- `name`: human-readable name
- `import_source`: the import_source tag used in portfolio metadata
- `configured`: bool — whether credentials are set in environment
- `description`: brief description of what inventory the SSP covers

**`import_deals_pubmatic(status, deal_type, page_size)`**
- `status`: filter — `"active"`, `"inactive"`, `"pending"`, or `"all"` (default `"all"`)
- `deal_type`: filter — `"PMP"`, `"PG"`, `"preferred"`, or `"all"` (default `"all"`)
- `page_size`: int, default 100
- Returns: same structure as `import_deals_csv` (total_rows, successful, failed, skipped, errors, deal_ids, timestamp)

**`import_deals_magnite(platform, status, page_size)`**
- `platform`: `"streaming"` (CTV) or `"dv_plus"` (display/video), default `"streaming"`
- `status`: filter, default `"all"`
- `page_size`: int, default 100
- Returns: same structure as `import_deals_csv`

**`import_deals_index_exchange(status, deal_type, page_size)`**
- `status`: filter, default `"all"`
- `deal_type`: filter, default `"all"`
- `page_size`: int, default 100
- Returns: same structure as `import_deals_csv`

### 7.3 Credential management

SSP connector credentials are configured via environment variables:

```bash
# PubMatic
PUBMATIC_API_TOKEN=<bearer_token>
PUBMATIC_SEAT_ID=<seat_id>

# Magnite
MAGNITE_ACCESS_KEY=<access_key>
MAGNITE_SECRET_KEY=<secret_key>
MAGNITE_SEAT_ID=<seat_id>

# Index Exchange
IX_API_KEY=<api_key>
IX_SEAT_ID=<seat_id>
```

If credentials for an SSP are not set, the corresponding MCP tool returns an informative error (rather than raising an exception) and `list_ssp_connectors` marks that connector as `configured: false`.

---

## 8. Testing Strategy

Testing without real SSP credentials is possible via two complementary approaches:

### 8.1 Unit tests with response fixtures

Each connector ships with a fixture file containing a realistic sample API response based on the SSP's documented response format:

```
tests/unit/fixtures/
  pubmatic_deals_response.json    # Sample PubMatic API response
  magnite_deals_response.json     # Sample Magnite API response
  index_exchange_deals_response.json
```

Unit tests mock `requests.get` (or the session auth call) and assert that:
1. The connector correctly maps each response field to the right DealStore kwarg
2. Missing or null fields are handled gracefully (None, not KeyError)
3. Deal type and media type normalization works for all known SSP values
4. Deduplication skips duplicate `seller_deal_id` within a single fetch
5. `import_source` is set correctly in portfolio metadata

### 8.2 Integration tests with a mock HTTP server

For end-to-end tests of the fetch → normalize → save pipeline, use `pytest`'s `httpretty` or `responses` library to mock the SSP HTTP endpoints:

```python
@responses.activate
def test_pubmatic_connector_end_to_end(deal_store):
    responses.add(
        responses.GET,
        "https://api.pubmatic.com/pmp/deals",
        json=load_fixture("pubmatic_deals_response.json"),
        status=200,
    )
    connector = PubMaticConnector(api_token="test-token", seat_id="test-seat")
    result = connector.fetch_deals(status="active")
    assert result.successful == len(load_fixture("pubmatic_deals_response.json")["deals"])
    # Verify deals saved to DealStore
    ...
```

### 8.3 MCP tool tests

Mirror the existing pattern from `tests/unit/test_mcp_deal_library.py`. Test that:
1. `list_ssp_connectors` returns all three connectors
2. Each `import_deals_*` tool returns the standard result structure
3. Unconfigured connector returns a clear error message (no credentials)
4. Configured connector (with mocked HTTP) saves deals and returns correct counts

### 8.4 What does NOT need testing

- Real SSP API call success (requires live credentials — not testable in CI)
- SSP rate limiting behavior (document it, don't test it)
- Deal sync timing/scheduling (connectors are on-demand, not scheduled)

---

## 9. Seller System SSP Modeling (Reference)

The seller system (`ad_seller_system`) references SSPs in context of:
- `linear_tv_inventory_agent.py`: "Resellers/SSPs (PubMatic, Magnite, GumGum)" — describes SSPs as resellers of linear TV inventory
- `product_setup_flow.py`: Creates a sample "Linear TV — Reseller/SSP (PubMatic/Magnite)" product
- `ad_server_base.py`: Models FreeWheel as an ad server type alongside Google Ad Manager
- `settings.py`: Has `freewheel_api_url` / `freewheel_api_key` configuration for FreeWheel ad server integration

The seller system models SSPs as **distribution channels** (publishers selling through SSPs), not as entities the buyer directly connects to. The buyer agent's SSP connectors complement this: they represent the buyer's view of the same SSPs — the buyer imports deals that SSPs surface on behalf of publishers.

There is no overlap or conflict between the seller's SSP modeling and the buyer's SSP connectors. They operate on opposite sides of the transaction.

---

## 10. Implementation Order and Dependencies

The implementation order follows buyer-side API accessibility (most accessible first):

```
buyer-ssp-base: SSP Connector base class and interface
    |
    +-- buyer-ssp-pm: PubMatic SSP connector (Priority 1 -- best buyer API)
    |       |
    |       +-- buyer-ssp-mcp: SSP connector MCP tools
    |
    +-- buyer-ssp-mg: Magnite SSP connector (Priority 2 -- largest market share, DSP-mediated)
    |
    +-- buyer-ssp-ix: Index Exchange connector (Priority 3 -- publisher-side only)
```

**Dependency rule:** All three SSP connectors depend on the base class. The MCP tools bead depends on at least the PubMatic connector (to validate the interface) but can be developed alongside it in practice.

---

## 11. Out of Scope for v2

- **FreeWheel:** Not a traditional SSP. It is a publisher-side ad server. Buyer-side integration pattern is unclear; deferred until the use case is better defined.
- **Google AdX:** Requires Google Ads API and Cloud project setup — significant overhead for a reference implementation.
- **OpenDirect 2.1:** Standard exists but real-world SSP adoption is near-zero.
- **Deal proposals / RFPs:** Buyer-initiated deal creation does not exist in standard programmatic workflows (Google's `sendRfp` is the exception, and it is Google-specific).
- **IAB Deals API v1.0 receiving end:** The formal standard (February 2026) is the right long-term protocol for deal sync, but v2 uses proprietary SSP APIs because they are more mature and better documented for reference implementation purposes. IAB Deals API v1.0 receiver support is a v3 enhancement.
- **Scheduled sync / webhooks:** Connectors are on-demand (user triggers import via MCP tool). Scheduled/automatic sync and webhook-based deal push are deferred to v3.
