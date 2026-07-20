# ChatGPT, Codex & AI IDE Setup Guide

Connect your buyer agent to ChatGPT, OpenAI Codex, Cursor, Windsurf, or any MCP-compatible AI assistant.

## Prerequisites

Same as [Claude Desktop Setup](claude-desktop-setup.md) — your developer must have deployed the buyer agent and generated credentials.

Your buyer agent MCP endpoint: `https://your-buyer.example.com/mcp` (Streamable HTTP — canonical)

> **Legacy SSE fallback**: Older MCP clients that require SSE transport can connect to `https://your-buyer.example.com/mcp-sse/sse` instead.

!!! warning "Authentication is X-API-Key, not Bearer"
    When the server has `API_KEY` set, every request must carry the key in the **`X-API-Key` header**. The buyer agent does not read `Authorization: Bearer` tokens — a Bearer-only client will always get `401`. Use a client that supports custom headers (configs below), or run without `API_KEY` in trusted/dev environments.

---

## ChatGPT

ChatGPT natively supports MCP servers via Developer Mode.

### Step 1: Enable Developer Mode

1. Open [chatgpt.com](https://chatgpt.com)
2. Go to **Settings > Apps & Connectors > Advanced settings**
3. Toggle **Developer Mode** on

> Available on Plus, Pro, Business, Enterprise, and Education plans.

### Step 2: Add the Buyer Agent

1. Go to **Settings > Connectors** (or **Settings > Apps**)
2. Click **Create**
3. Enter your MCP server URL: `https://your-buyer.example.com/mcp`

    > ChatGPT connectors cannot send a custom `X-API-Key` header. Connect to a deployment running without `API_KEY` (trusted network), or front the agent with a proxy that injects the header.

4. Name it: `Buyer Agent`
5. Add a description: `Manage campaigns, deals, pacing, approvals, and seller relationships`
6. Click **Create**

### Step 3: Use in a Chat

1. Start a new chat
2. Click the **+** button at the bottom
3. Select your buyer agent from the **More** menu
4. Start chatting: *"List my active campaigns"* or *"Show me available CTV inventory from ESPN"*

ChatGPT will call your buyer agent's MCP tools and show the results inline.

---

## OpenAI Codex

Codex supports MCP servers via its config file.

### Option A: CLI

```bash
codex mcp add buyer-agent --url https://your-buyer.example.com/mcp
```

### Option B: Config File

Edit `~/.codex/config.toml` (global) or `.codex/config.toml` (project):

```toml
[mcp_servers.buyer-agent]
url = "https://your-buyer.example.com/mcp"

[mcp_servers.buyer-agent.http_headers]
X-API-Key = "sk-operator-XXXXX"
```

> Do **not** use `bearer_token_env_var` — it sends an `Authorization: Bearer` header, which the buyer agent ignores (guaranteed `401` when `API_KEY` is set). If your Codex version does not support custom HTTP headers, connect to a deployment without `API_KEY` or use a header-injecting proxy.

### Verify

In Codex, type `/mcp` to see connected servers and available tools.

---

## Cursor

Cursor supports MCP servers on all plans (including free).

### Option A: Project-Level Config

Create `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "buyer-agent": {
      "url": "https://your-buyer.example.com/mcp",
      "headers": {
        "X-API-Key": "sk-operator-XXXXX"
      }
    }
  }
}
```

### Option B: Global Config

Create `~/.cursor/mcp.json` with the same format.

### Using Environment Variables

```json
{
  "mcpServers": {
    "buyer-agent": {
      "url": "https://your-buyer.example.com/mcp",
      "headers": {
        "X-API-Key": "${env:BUYER_AGENT_API_KEY}"
      }
    }
  }
}
```

---

## Windsurf

Windsurf supports MCP via its Cascade panel.

### Option A: MCP Marketplace

1. Click the **MCPs icon** in the top-right of the Cascade panel
2. Search for your buyer agent or click **Add Custom**
3. Enter the MCP URL and credentials

### Option B: Config File

Edit `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "buyer-agent": {
      "serverUrl": "https://your-buyer.example.com/mcp",
      "headers": {
        "X-API-Key": "sk-operator-XXXXX"
      }
    }
  }
}
```

---

## Available Tools (All Platforms)

Once connected, all platforms have access to the same 43 MCP tools:

| Category | Examples |
|----------|---------|
| **Foundation** | `get_setup_status`, `health_check`, `get_config` |
| **Setup Wizard** | `run_setup_wizard`, `get_wizard_step`, `complete_wizard_step`, `skip_wizard_step` |
| **Campaign Management** | `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets` |
| **Deal Library** | `list_deals`, `search_deals`, `inspect_deal`, `import_deals_csv`, `create_deal_manual` |
| **Seller Discovery** | `discover_sellers`, `get_seller_media_kit`, `compare_sellers` |
| **Negotiation** | `start_negotiation`, `get_negotiation_status`, `list_active_negotiations` |
| **Orders** | `list_orders`, `get_order_status`, `transition_order` |
| **Templates** | `list_templates`, `create_template`, `instantiate_from_template` |
| **Reporting** | `get_deal_performance`, `get_campaign_report`, `get_pacing_report` |
| **Approvals** | `list_pending_approvals`, `approve_or_reject` |
| **API Keys** | `list_api_keys`, `create_api_key`, `revoke_api_key` |
| **SSP Connectors** | `list_ssp_connectors`, `import_deals_ssp`, `test_ssp_connection` |
| **Contextual Enrichment (Mixpeek)** | `classify_content`, `contextual_search`, `check_brand_safety` |

See the [MCP Tools Reference](ai-assistant/mcp-tools.md) for the full tool catalog.

---

## REST API Alternative

If your platform doesn't support MCP, a subset of operations is available over the REST API:

```
Base URL: https://your-buyer.example.com
Auth header: X-API-Key: sk-operator-XXXXX
```

The REST surface covers bookings (submit/status/approve), product search, events, and buyer orders — the deal library, wizard, negotiation, and reporting tools are MCP-only. See the [API Overview](api/overview.md).

For ChatGPT specifically, you can also create a **Custom GPT** with Actions pointing to the REST API's OpenAPI spec at `https://your-buyer.example.com/openapi.json`.
