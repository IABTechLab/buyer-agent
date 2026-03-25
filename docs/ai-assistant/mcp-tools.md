# MCP Tools Reference

Complete catalog of all MCP tools exposed by the buyer agent at `/mcp/sse`. Tools are organized by category. All tools return JSON.

---

## Foundation

Core tools for checking system state. Start here to verify your connection.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `health_check` | _(none)_ | Overall service health: status (`healthy`, `degraded`, `unhealthy`), version, and per-service details (database, seller connections, event bus). |
| `get_setup_status` | _(none)_ | Configuration completeness check. Reports whether seller endpoints, database, API key, and LLM are configured. Returns `setup_complete: true/false`. |
| `get_config` | _(none)_ | Non-sensitive configuration values: environment, seller endpoints, database URL, LLM model names, temperature, log level. API keys are never exposed. |

---

## Setup Wizard

Four tools for running the two-phase guided configuration wizard. See [Setup Wizard](setup-wizard.md) for a walkthrough.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `run_setup_wizard` | _(none)_ | Auto-detects completed steps from existing config, then returns the full wizard state: all 8 steps, progress percentage, and current phase. |
| `get_wizard_step` | `step_number` (int, 1–8) | Detailed information for a specific step: title, description, phase, config fields, defaults, and current status. |
| `complete_wizard_step` | `step_number` (int), `config` (JSON string) | Mark a step complete with the given configuration values. Returns success/failure. |
| `skip_wizard_step` | `step_number` (int, 1–7) | Skip a step and apply its defaults. Step 8 (Review & Launch) cannot be skipped. |

---

## Campaign Management

Query and monitor campaigns managed by the buyer agent.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_campaigns` | `status` (string, optional) | List all campaigns, optionally filtered by status: `DRAFT`, `PLANNING`, `BOOKING`, `READY`, `ACTIVE`, `PAUSED`, `COMPLETED`, `CANCELED`. |
| `get_campaign_status` | `campaign_id` (string) | Full campaign detail: name, status, budget, flight dates, channel breakdown, and latest pacing snapshot. |
| `check_pacing` | `campaign_id` (string) | Pacing verdict for a campaign: `on_track` (within ±10%), `behind` (<−10%), `ahead` (>+10%), or `no_data`. Includes spend vs expected and per-channel breakdown. |
| `review_budgets` | _(none)_ | Aggregate budget and spend across all campaigns. Returns total budget, total spend, overall delivery percentage, and per-campaign breakdowns. |

---

## Deal Library

Browse, search, and manage the deal portfolio.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_deals` | `status` (optional), `deal_type` (optional), `media_type` (optional), `seller_domain` (optional), `limit` (int, default 50) | List deals with optional filters. Deal types: `PG`, `PD`, `PA`, `OPEN_AUCTION`, `UPFRONT`, `SCATTER`. Media types: `DIGITAL`, `CTV`, `LINEAR_TV`, `AUDIO`, `DOOH`. |
| `search_deals` | `query` (string) | Free-text search across display name, description, seller org, and seller domain. Returns matching deals with the fields they matched in. |
| `inspect_deal` | `deal_id` (string) | Full deal detail: all fields, portfolio metadata (import source, advertiser, tags), cross-platform activations, and cached performance metrics. |
| `import_deals_csv` | `csv_data` (string), `default_seller_url` (string, optional), `default_product_id` (string, optional) | Import deals from CSV text. Auto-detects column mapping. Returns counts of successful, failed, and skipped rows, plus any per-row errors. |
| `create_deal_manual` | `display_name` (string), `seller_url` (string), `deal_type` (string, default `PD`), `status` (string, default `draft`), plus optional: `media_type`, `price`, `impressions`, `flight_start`, `flight_end`, `seller_deal_id`, `seller_org`, `seller_domain`, `price_model`, `fixed_price_cpm`, `bid_floor_cpm`, `currency`, `description`, `advertiser_id`, `tags` | Create a single deal manually. Validates input and saves with `import_source=MANUAL` metadata. |
| `get_portfolio_summary` | `top_sellers_count` (int, default 5), `expiring_within_days` (int, default 30) | Portfolio-wide stats: total deals, estimated value, counts by status/deal type/media type, top sellers by deal count, and deals expiring soon. |

---

## Seller Discovery

Find and compare seller agents.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `discover_sellers` | `capability` (string, optional) | Query the IAB AAMP registry for available seller agents. Filter by capability (e.g., `ctv`, `display`, `video`). Returns agent IDs, names, URLs, capabilities, trust level, and protocols. |
| `get_seller_media_kit` | `seller_url` (string) | Fetch a seller's media kit: available packages, ad formats, device types, pricing ranges, geo targets, and featured packages. |
| `compare_sellers` | `seller_urls` (list of strings) | Side-by-side comparison across multiple sellers. Fetches each media kit and summarizes packages, ad formats, and pricing. Unreachable sellers are noted in the response. |

---

## Negotiation

Start and track price negotiations with sellers.

!!! note
    `start_negotiation` creates a deal in `negotiating` status within the Agent Range demo ecosystem. Real SSP integrations use seller-initiated deal flows.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `start_negotiation` | `seller_url` (string), `product_id` (string), `product_name` (string, optional), `initial_price` (float, optional) | Initiate a negotiation: creates a deal in `negotiating` status and records the opening offer as round 1. Returns the new `deal_id`. |
| `get_negotiation_status` | `deal_id` (string) | Current deal status plus full negotiation history (all rounds: buyer price, seller price, action, rationale). |
| `list_active_negotiations` | _(none)_ | All deals currently in `negotiating` status with round counts and timestamps. |

---

## Orders

Manage order lifecycle.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_orders` | `status` (string, optional) | List orders, optionally filtered by status: `pending`, `booked`, `delivering`, `completed`, `cancelled`. |
| `get_order_status` | `order_id` (string) | Full order detail including all metadata. |
| `transition_order` | `order_id` (string), `to_status` (string), `reason` (string, optional) | Trigger an order state transition. Records the previous status and reason. |

---

## Templates

Create reusable deal and supply path templates.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_templates` | `template_type` (string, optional: `deal` or `supply_path`) | List deal templates and/or supply path templates. Returns both by default. |
| `create_template` | `template_type` (string, required: `deal` or `supply_path`), `name` (string, required), plus type-specific fields (see below) | Create a new template. Deal template fields: `deal_type_pref`, `max_cpm`, `min_impressions`, `default_price`, `default_flight_days`, `advertiser_id`, `agency_id`. Supply path template fields: `max_reseller_hops`, `scoring_weights`, `preferred_ssps`, `blocked_ssps`. |
| `instantiate_from_template` | `template_id` (string), `overrides` (JSON string or dict, optional) | Create a deal from a deal template. Overrides can change `price`, `product_name`, `product_id`, `seller_url`. |

---

## Reporting

Campaign and deal performance reports.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_deal_performance` | `deal_id` (string) | Deal metrics: price, status, negotiation round count, and timestamps. |
| `get_campaign_report` | `campaign_id` (string) | Comprehensive campaign report combining status summary, pacing data, creative asset validation counts (total/valid/pending/invalid), and deal-level metrics. |
| `get_pacing_report` | `campaign_id` (string) | Detailed pacing report with expected vs actual spend, per-channel breakdown (including eCPM and fill rate), deviation alerts, and pacing status verdict. |

---

## Approval

Review and act on pending human-in-the-loop approval requests.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_pending_approvals` | `campaign_id` (string, optional) | List approval requests awaiting a decision. Filter by campaign or get all pending. Returns stage, status, request time, and context for each. |
| `approve_or_reject` | `approval_request_id` (string), `decision` (string: `approved` or `rejected`), `reviewer` (string), `reason` (string, optional) | Record an approval decision. Rejects requests that are already decided. |

---

## API Keys

Manage API keys used for seller integrations.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_api_keys` | _(none)_ | List all configured seller API keys. Full key values are never exposed — only the seller URL and a masked version of the key (last 4 characters visible). |
| `create_api_key` | `seller_url` (string), `api_key` (string) | Store or replace an API key for a seller. Response confirms creation with a masked key. |
| `revoke_api_key` | `seller_url` (string) | Remove a stored API key. Returns `revoked: true` if a key was found and removed. |

---

## SSP Connectors

Import deals directly from supply-side platforms.

Three SSP connectors are supported: **PubMatic**, **Magnite**, and **Index Exchange**. Each requires credentials set as environment variables — use `list_ssp_connectors` to see which variables are needed.

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_ssp_connectors` | _(none)_ | List all available connectors with configuration status. Shows display name, whether required environment variables are set, and which variables are needed. |
| `import_deals_ssp` | `ssp_name` (string: `pubmatic`, `magnite`, or `index_exchange`) | Fetch deals from the named SSP, normalize them to the deal store schema, and import. Returns the same result structure as `import_deals_csv`. |
| `test_ssp_connection` | `ssp_name` (string) | Check whether the named SSP connector is configured and, if so, attempt a lightweight API call to verify the credentials. |
