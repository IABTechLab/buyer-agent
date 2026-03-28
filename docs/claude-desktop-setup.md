# Claude Setup Guide (Desktop & Web)

Connect your buyer agent to Claude Desktop or Claude on the web for conversational management of campaigns, deals, pacing, seller relationships, and approvals.

## Prerequisites

Your developer should have already:
- Deployed the buyer agent server
- Connected your seller agents (configured `SELLER_ENDPOINTS`)
- Configured SSP connectors if needed (PubMatic, Magnite, Index Exchange)
- Generated an operator API key for you

If not, see the [Developer Setup Guide](ai-assistant/developer-setup.md) first.

## Step 1: Add the Buyer Agent to Claude Desktop

There are two ways to connect, depending on whether the buyer agent is running locally or on a remote server.

### Option A: Remote Server (Recommended for Production)

Works on both **Claude Desktop** and **Claude on the web** (claude.ai):

1. Open Claude Desktop or go to [claude.ai](https://claude.ai)
2. Go to **Settings > Integrations**
3. Click **"+ Add Custom Integration"**
4. Enter your buyer agent's MCP URL: `https://your-buyer.example.com/mcp/sse`
5. If prompted for authentication, enter your operator API key
6. Click **Save**

> Available on Pro, Max, Team, and Enterprise plans. Free users get one custom integration. This is the same setup for both Claude Desktop and Claude web — the integration syncs across both.

### Option B: Local Development Server

For buyer agents running on `localhost`:

1. Open Claude Desktop
2. Go to **Settings > Developer > Edit Config**
3. This opens `claude_desktop_config.json`. Add:

```json
{
  "mcpServers": {
    "buyer-agent": {
      "url": "http://localhost:8001/mcp/sse"
    }
  }
}
```

4. Save and restart Claude Desktop

> **Note**: The JSON config method is for **local servers only**. Remote servers must use the Settings > Integrations UI.

## Step 2: First-Run Setup Wizard

When you first connect, the buyer agent detects that configuration is incomplete and offers to walk you through it:

> **Claude**: "Welcome! Your buyer agent is deployed and connected. Let's finish the business setup. I'll walk you through the key steps — you can skip any step to accept defaults."

### Step 1: Buyer Identity

> **You**: "We're Havas Media Group"
>
> **Claude**: "Got it. I've set your buyer name to 'Havas Media Group'. What's your seat ID for deal negotiations?"
>
> **You**: "havas-na-001"

### Step 2: Seller Connections

> **Claude**: "I see seller endpoints are configured. Let me verify connectivity."
>
> **Claude**: "Connected to 2 seller agents: ESPN Digital (espn.example.com) and Condé Nast (conde.example.com). Both are healthy."

### Step 3: Deal Templates

> **Claude**: "Want to set up deal templates for common buy types? I can pre-configure CTV, Video, and Display templates."
>
> **You**: "Yes — CTV at $28 max CPM and Video at $18 max CPM to start"

### Step 4: Approval Gates

> **Claude**: "Should deals require approval before they go live? What's your threshold?"
>
> **You**: "Auto-approve deals under $10,000 total value. Require approval for larger deals."

### Step 5: SSP Connectors

> **Claude**: "I see PubMatic credentials are configured. Want me to run an initial deal import?"
>
> **You**: "Yes, pull in all active deals"

### Step 6: Review & Launch

> **Claude**: "Here's your setup summary: [summary]. Your buyer agent is ready. Try 'List active campaigns' or 'Show me available inventory from ESPN' to get started."

## Available Slash Commands

Once the buyer agent is connected, Claude Desktop recognizes the following slash commands:

| Command | What it does |
|---------|-------------|
| `/setup` | Run the guided setup wizard (first-time or reconfigure) |
| `/status` | Check configuration and system health |
| `/campaigns` | Campaign portfolio overview with budget pacing |
| `/deals` | Full dashboard of your deal portfolio |
| `/discover` | Find and compare seller agents in the IAB registry |
| `/negotiate` | View active negotiations and start new ones |
| `/orders` | Active orders and execution status |
| `/approvals` | Pending items waiting for your decision |
| `/configure` | Manage templates, SSP connectors, and settings |
| `/help` | List all available capabilities |

See the [Slash Commands Reference](ai-assistant/slash-commands.md) for detailed descriptions and the tools each command uses.

---

## Day-to-Day Operations

After setup, use Claude Desktop to manage your buyer agent:

### Campaigns
- "List all active campaigns"
- "What's the pacing on campaign camp-abc123?"
- "Review budgets across all campaigns — which ones are underspending?"
- "Give me a full report on camp-abc123"

### Deals
- "Find available CTV inventory from ESPN"
- "Create a PMP deal with ESPN for $28 CPM on their sports package"
- "How is deal deal-xyz456 performing?"
- "Import available deals from PubMatic"
- "Show me my deal library"

### Approvals
- "Are there any deals waiting for my approval?"
- "Approve approval request appr-001 — reviewed and looks good"
- "Reject appr-002 — CPM is too high, needs renegotiation"

### Templates
- "List my deal templates"
- "Create a new Q3 CTV template at $25 max CPM"
- "Instantiate template tmpl-001 for the ESPN sports package"

### Reporting
- "Generate a pacing report for camp-abc123 with alerts"
- "Show deal performance for deal-xyz456"

### Seller Management
- "What sellers are configured and are they all reachable?"
- "Add an API key for the new seller at https://publisher.example.com"
- "Show me ESPN's media kit"

### Troubleshooting
- "Check the buyer agent health"
- "Show me the buyer setup status"
- "What sellers do I have configured?"

## Also Works With

The same MCP endpoint works with other AI platforms:

- **[ChatGPT](multi-client-setup.md)** — via Developer Mode Apps & Connectors
- **[OpenAI Codex](multi-client-setup.md#openai-codex)** — via `config.toml`
- **[Cursor](multi-client-setup.md#cursor)** — via `.cursor/mcp.json`
- **[Windsurf](multi-client-setup.md#windsurf)** — via MCP config file

## Troubleshooting

### Claude says "no tools available" or does not recognize buyer agent tools

1. Confirm the buyer server is running: `curl http://localhost:8001/health`
2. Check that `claude_desktop_config.json` has the correct URL (default port is 8001)
3. Fully quit and relaunch Claude Desktop — it only reads the config at startup
4. Check Claude Desktop logs for connection errors (macOS: `~/Library/Logs/Claude/`)

### Connection refused on `http://localhost:8001/mcp/sse`

The buyer server is not running or crashed. Start it with:

```bash
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

If it fails to start, check that your `.env` has a valid `ANTHROPIC_API_KEY` and that all dependencies are installed (`pip install -e .`).

### Tools appear but calls return errors

**`seller_endpoints_configured: false`** — No sellers are configured. Set `SELLER_ENDPOINTS=http://localhost:3000` in your `.env` and restart the server.

**`database_accessible: false`** — The SQLite database file cannot be created or opened. Check that the process has write permission in the project directory.

**Campaign not found** — Use `list_campaigns` first to see valid IDs.

**Approval request not found** — Use `list_pending_approvals` to confirm the request ID before calling `approve_or_reject`.
