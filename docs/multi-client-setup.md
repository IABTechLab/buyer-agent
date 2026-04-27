# ChatGPT, Codex & AI IDE Setup Guide

Connect your buyer agent to ChatGPT, OpenAI Codex, Cursor, Windsurf, or any MCP-compatible AI assistant.

## Prerequisites

Same as [Claude Desktop Setup](claude-desktop-setup.md) — your developer must have deployed the buyer agent and generated credentials.

Your buyer agent MCP endpoint: `https://your-buyer.example.com/mcp` (Streamable HTTP — canonical)

> **Legacy SSE fallback**: Older MCP clients that require SSE transport can connect to `https://your-buyer.example.com/mcp-sse/sse` instead.

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
bearer_token_env_var = "BUYER_AGENT_API_KEY"
```

Set the environment variable:

```bash
export BUYER_AGENT_API_KEY="sk-operator-XXXXX"
```

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
        "Authorization": "Bearer sk-operator-XXXXX"
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
        "Authorization": "Bearer ${env:BUYER_AGENT_API_KEY}"
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
        "Authorization": "Bearer sk-operator-XXXXX"
      }
    }
  }
}
```

---

## Available Tools (All Platforms)

Once connected, all platforms have access to the same 40+ MCP tools across 12 categories:

| Category | Examples |
|----------|---------|
| **Foundation** | `get_setup_status`, `health_check`, `get_config` |
| **Setup Wizard** | `run_setup_wizard`, `get_wizard_status` |
| **Campaign Management** | `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets` |
| **Deal Library** | `list_deals`, `search_deals`, `import_deal`, `create_deal` |
| **Seller Discovery** | `list_sellers`, `browse_media_kit`, `compare_inventory` |
| **Negotiation** | `start_negotiation`, `get_negotiation_status` |
| **Orders** | `list_orders`, `get_order_status` |
| **Templates** | `list_templates`, `create_template`, `instantiate_from_template` |
| **Reporting** | `get_deal_performance`, `get_campaign_report`, `get_pacing_report` |
| **Approvals** | `list_pending_approvals`, `approve_or_reject` |
| **API Keys** | `list_api_keys`, `create_api_key`, `revoke_api_key` |
| **SSP Connectors** | `import_from_pubmatic`, `import_from_magnite`, `import_from_index_exchange` |

See the [MCP Tools Reference](ai-assistant/mcp-tools.md) for the full tool catalog.

---

## REST API Alternative

If your platform doesn't support MCP, you can use the REST API directly:

```
Base URL: https://your-buyer.example.com
Auth: Authorization: Bearer sk-operator-XXXXX
```

The buyer agent exposes REST endpoints for all major operations. See the [API Overview](api/overview.md).

For ChatGPT specifically, you can also create a **Custom GPT** with Actions pointing to the REST API's OpenAPI spec at `https://your-buyer.example.com/openapi.json`.
