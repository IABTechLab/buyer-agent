# Claude Desktop Setup Guide

Connect Claude Desktop to the IAB Tech Lab Ad Buyer Agent via the Model Context
Protocol (MCP). Once connected, you can manage campaigns, review deals, check
pacing, and interact with seller systems using natural language directly inside
Claude Desktop.

---

## Prerequisites

- **Claude Desktop** installed (macOS or Windows). Download from
  [claude.ai/download](https://claude.ai/download).
- **Ad Buyer System running** locally. See the
  [Quickstart](getting-started/quickstart.md) if you have not done this yet.
  The server must be reachable at `http://localhost:8001` before Claude Desktop
  can connect to it.
- **Python 3.11+** with the buyer agent installed (`pip install -e .`).

---

## How the MCP Connection Works

The buyer agent exposes an MCP server over
[Server-Sent Events (SSE)](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events).
When Claude Desktop starts it reads `claude_desktop_config.json`, connects to
the SSE endpoint, and makes all registered tools available in the Claude chat
interface.

```
Claude Desktop  --SSE-->  http://localhost:8001/mcp/sse  -->  Buyer Agent
```

The SSE endpoint is mounted automatically when the buyer server starts — no
extra configuration on the server side is needed.

---

## Step 1: Start the Buyer Agent Server

Open a terminal and start the server:

```bash
cd /path/to/ad_buyer_system
source venv/bin/activate        # or: .venv/bin/activate
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

Verify it is running:

```bash
curl http://localhost:8001/health
# {"status": "healthy", "version": "..."}
```

Leave this terminal open. Claude Desktop needs the server running at all times.

---

## Step 2: Configure Claude Desktop

Claude Desktop is configured via a JSON file. Open it in a text editor:

| Platform | Path |
|----------|------|
| macOS    | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows  | `%APPDATA%\Claude\claude_desktop_config.json` |

If the file does not exist, create it. Add the `mcpServers` block below. If the
file already has other MCP servers, add `ad-buyer-agent` alongside them.

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse"
    }
  }
}
```

### What each field means

| Field | Value | Description |
|-------|-------|-------------|
| `mcpServers` | object | Top-level registry of MCP servers for Claude Desktop |
| `ad-buyer-agent` | key | Display name shown in Claude Desktop's tool panel |
| `url` | `http://localhost:8001/mcp/sse` | SSE endpoint of the running buyer agent |

### Using a non-default port

If you started the server on a different port, update the URL accordingly:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:9000/mcp/sse"
    }
  }
}
```

---

## Step 3: Restart Claude Desktop

After saving `claude_desktop_config.json`, **fully quit and relaunch** Claude
Desktop. It reads the config only on startup.

- macOS: `Cmd+Q` then reopen from Applications
- Windows: Right-click the system tray icon and choose Quit, then reopen

When Claude Desktop reconnects, look for a hammer icon or "Tools" panel
indicating that MCP tools are available.

---

## Step 4: Verify the Connection

In a new Claude conversation, type:

```
Check the health of the buyer agent.
```

Claude will call `health_check` and return something like:

```
The buyer agent is healthy (version 1.0.0). All services are running:
database: healthy, seller_connections: configured (1 endpoint), event_bus: healthy.
```

If Claude says it cannot find any tools, see the [Troubleshooting](#troubleshooting)
section below.

---

## Available Tools

The buyer agent exposes tools across six categories. All tools are available
immediately once Claude Desktop connects — no additional setup is required.

### Foundation

These tools let you inspect system state and configuration.

| Tool | Description |
|------|-------------|
| `get_setup_status` | Check whether all required configuration is in place (seller endpoints, database, API keys, LLM key) |
| `health_check` | Check service health: database, seller connections, event bus |
| `get_config` | View non-sensitive configuration (environment, models, seller URLs, log level) |

### Campaign Management

View and monitor advertising campaigns and their delivery status.

| Tool | Description |
|------|-------------|
| `list_campaigns` | List all campaigns, optionally filtered by status (DRAFT, ACTIVE, PAUSED, etc.) |
| `get_campaign_status` | Get full details for a specific campaign, including the latest pacing snapshot |
| `check_pacing` | Check whether a campaign is on-track, behind, or ahead of expected spend |
| `review_budgets` | Aggregate budget and spend across all campaigns |

### Approval Management

Review and decide on pending approval requests for deals and campaigns.

| Tool | Description |
|------|-------------|
| `list_pending_approvals` | List approval requests awaiting a decision, optionally filtered by campaign |
| `approve_or_reject` | Approve or reject a specific approval request with an optional reason |

### API Key Management

Store and manage API keys used to authenticate with seller agents.

| Tool | Description |
|------|-------------|
| `list_api_keys` | List configured seller API keys (key values are always masked) |
| `create_api_key` | Store or replace an API key for a seller URL |
| `revoke_api_key` | Permanently remove a stored API key |

### Templates

Create reusable deal and supply-path templates to speed up booking.

| Tool | Description |
|------|-------------|
| `list_templates` | List deal templates and supply path templates (filterable by type) |
| `create_template` | Create a new deal or supply path template |
| `instantiate_from_template` | Instantiate a deal from a template with optional field overrides |

### Reporting

Generate performance and pacing reports for campaigns and deals.

| Tool | Description |
|------|-------------|
| `get_deal_performance` | Get price, status, and negotiation history for a specific deal |
| `get_campaign_report` | Full campaign report: status, pacing, creative validation, deal metrics |
| `get_pacing_report` | Detailed pacing report with per-channel breakdown and deviation alerts |

---

## First-Use Walkthrough

This walkthrough uses the MCP tools to verify your installation and explore the
system for the first time. Run each step in a new Claude Desktop conversation.

### Step 1 — Confirm setup status

```
Check the buyer agent setup status and tell me what's configured.
```

Claude calls `get_setup_status`. A fresh installation will show
`seller_endpoints_configured: false` until you add a seller URL. That is
expected at this point.

### Step 2 — View the current configuration

```
Show me the buyer agent configuration.
```

Claude calls `get_config` and lists the active environment, model names,
seller endpoints, and database path. This is a safe read-only call — no
secrets are exposed.

### Step 3 — Add a seller endpoint

If you have a seller agent running (default port 3000), add its API key:

```
Store an API key for the seller agent at http://localhost:3000. Use the key "demo-key-001".
```

Claude calls `create_api_key`. It confirms the key was stored and shows the
masked value. The buyer will use this key for authenticated calls to that seller.

### Step 4 — Check for campaigns

```
List all campaigns. If there are none, that's fine — just tell me the count.
```

Claude calls `list_campaigns` and reports the total. On a fresh database this
returns zero campaigns.

### Step 5 — Review pending approvals

```
Are there any pending approval requests?
```

Claude calls `list_pending_approvals`. On a fresh system this is empty. Once
the booking workflow creates deals that require approval, they will appear here.

---

## Example Conversations

The examples below show natural-language prompts you can use for each tool
category. Copy and paste them into Claude Desktop after connecting.

### System health and configuration

```
Is the buyer agent healthy right now?
```

```
What sellers are configured and are they all reachable?
```

```
Show me the full configuration — environment, models, and endpoints.
```

### Campaign status and pacing

```
List all my active campaigns.
```

```
What is the pacing status for campaign camp-abc123?
```

```
Give me a full report on campaign camp-abc123 including pacing and deal metrics.
```

```
Review budgets across all campaigns and tell me which ones are underspending.
```

```
Which campaigns are behind on pacing by more than 15%?
```

### Approvals

```
Are there any deals or campaigns waiting for my approval?
```

```
Show me all pending approvals for campaign camp-abc123.
```

```
Approve approval request appr-001 on behalf of "jane.smith" with the reason "reviewed and looks good".
```

```
Reject approval request appr-002. Reason: CPM is too high, needs renegotiation.
```

### API key management

```
What seller API keys do I have configured?
```

```
Add an API key for http://seller.example.com:3000 with value "sk-seller-xyz".
```

```
Remove the API key for http://old-seller.example.com.
```

### Templates

```
List all my deal templates.
```

```
Show me the supply path templates I have set up.
```

```
Create a new PMP deal template called "Q3 CTV Standard" with a max CPM of $25.
```

```
Instantiate deal template tmpl-001 for product "premium-ctv" at a price of $22.50.
```

### Reporting

```
How is deal deal-abc456 performing?
```

```
Give me a pacing report for campaign camp-abc123 including any alerts.
```

```
Generate a full campaign report for camp-abc123.
```

---

## Troubleshooting

### Claude says "no tools available" or does not recognize buyer agent tools

1. Confirm the buyer server is running: `curl http://localhost:8001/health`
2. Check that `claude_desktop_config.json` uses the correct port (8001 by
   default, or whatever you passed to `--port`).
3. Fully quit and relaunch Claude Desktop — it only reads the config at startup.
4. Check the Claude Desktop logs for connection errors (macOS:
   `~/Library/Logs/Claude/`).

### Connection refused on `http://localhost:8001/mcp/sse`

The buyer server is not running or crashed. Start it with:

```bash
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

If it fails to start, check that your `.env` file has a valid
`ANTHROPIC_API_KEY` and that all dependencies are installed
(`pip install -e .`).

### Tools appear but calls return errors

**`database_accessible: false`** — The SQLite database file cannot be created
or opened. Check that the process has write permission in the project directory.
By default the database is `./ad_buyer.db` relative to where you launched the
server.

**`seller_endpoints_configured: false`** — No sellers are configured. Set
`SELLER_ENDPOINTS=http://localhost:3000` in your `.env` and restart the server.

**Campaign not found** — The campaign ID you provided does not exist in the
database. Use `list_campaigns` first to see valid IDs.

**Approval request not found** — Use `list_pending_approvals` to confirm the
request ID before calling `approve_or_reject`.

### API key was stored but seller calls still fail

Check whether the seller requires the key in a specific header. The buyer sends
stored keys as `X-Api-Key` on outbound requests. Confirm this matches the
seller's expected authentication scheme.

### Claude Desktop shows the tool panel but tools disappear after a few minutes

The SSE connection between Claude Desktop and the server may have timed out.
Restart the buyer server and use the `/reload` button in Claude Desktop's tool
panel if available, or restart Claude Desktop.

---

## Related

- [Quickstart](getting-started/quickstart.md) — Install and run the buyer agent
- [Configuration](guides/configuration.md) — Full environment variable reference
- [MCP Client (Seller Communication)](api/mcp-client.md) — How the buyer calls seller MCP tools
- [Campaign Automation Guide](guides/campaign-pipeline.md) — Full campaign lifecycle
- [Budget Pacing](guides/budget-pacing.md) — Understanding pacing thresholds and alerts
- [API Overview](api/overview.md) — REST API reference
