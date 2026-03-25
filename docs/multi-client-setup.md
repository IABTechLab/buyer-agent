# Multi-Client Setup Guide

The buyer agent's MCP server exposes buyer operations as tools via a Server-Sent Events (SSE) endpoint. Any MCP-compatible client --- Claude Desktop, Cursor, Windsurf, ChatGPT, and others --- can connect to this endpoint and use the buyer's tools conversationally.

This guide covers how to connect each client, what to expect from each integration, and how to manage multi-client access safely.

---

## How the Buyer MCP Server Works

The buyer agent runs as a FastAPI application with an MCP SSE server mounted at `/mcp/sse`:

```
http://localhost:8001/mcp/sse
```

Clients connect to this endpoint and discover tools automatically. The server is named `ad-buyer-agent` and exposes tools across these categories:

| Category | Tools |
|----------|-------|
| **Foundation** | `get_setup_status`, `health_check`, `get_config` |
| **Campaign Management** | `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets` |
| **Approvals** | `list_pending_approvals`, `approve_or_reject` |
| **API Key Management** | `list_api_keys`, `create_api_key`, `revoke_api_key` |
| **Templates** | `list_templates`, `create_template`, `instantiate_from_template` |
| **Reporting** | `get_deal_performance`, `get_campaign_report`, `get_pacing_report` |

Each tool accepts structured arguments and returns JSON. Full tool schemas are discoverable via the SSE connection --- no manual configuration of individual tools is needed.

---

## Starting the Buyer Agent

Before connecting any client, start the buyer agent server:

```bash
cd ad_buyer_system
uvicorn ad_buyer.interfaces.api.main:app --reload --port 8001
```

Verify it is running:

```bash
curl http://localhost:8001/health
# {"status": "healthy", "version": "1.0.0"}
```

The MCP SSE endpoint will be live at `http://localhost:8001/mcp/sse`.

---

## Claude Desktop

Claude Desktop has native MCP support and is the most straightforward client to configure.

### Configuration

Edit your Claude Desktop configuration file:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the buyer agent under `mcpServers`:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "command": "uvicorn",
      "args": [
        "ad_buyer.interfaces.api.main:app",
        "--port", "8001"
      ],
      "cwd": "/path/to/ad_buyer_system",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "SELLER_ENDPOINTS": "http://localhost:3000",
        "API_KEY": ""
      }
    }
  }
}
```

**Alternative: connect to an already-running server.**

If you prefer to manage the server separately (e.g., it runs as a system service or in a container), use the `url` form instead of `command`:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse"
    }
  }
}
```

Restart Claude Desktop after editing the config. The buyer tools will appear in Claude's tool panel.

### Verifying the Connection

In Claude Desktop, ask:

> "Check the buyer agent setup status."

Claude will call `get_setup_status` and return a JSON report showing whether seller endpoints, the database, and API keys are configured. If the response contains `"setup_complete": true`, the integration is working.

---

## ChatGPT

ChatGPT supports MCP via its **plugin** and **custom GPT** infrastructure. The buyer agent's SSE endpoint is compatible with ChatGPT's MCP connector when it is configured as a custom tool source.

### Current ChatGPT MCP Support

As of early 2026, ChatGPT's MCP support is delivered through:

1. **ChatGPT Desktop (macOS)** --- supports connecting to local MCP servers via a configuration file similar to Claude Desktop.
2. **ChatGPT Plugins / Custom Actions** --- supports REST-based tool calling via OpenAPI specs; MCP SSE is not directly supported through this path.

For the buyer agent, the **ChatGPT Desktop** path is the practical option for local use. The **plugin/custom action** path requires wrapping MCP tools in an OpenAPI spec, which is not covered here.

### ChatGPT Desktop Configuration

ChatGPT Desktop (macOS) uses a configuration file at:

```
~/Library/Application Support/ChatGPT/mcp_config.json
```

Add the buyer agent:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse"
    }
  }
}
```

Restart ChatGPT Desktop after saving.

!!! note "ChatGPT MCP Availability"
    ChatGPT Desktop MCP support is rolling out gradually. If the configuration file path above does not exist or the tools do not appear, check the [OpenAI documentation](https://openai.com) for the current MCP release status. The SSE endpoint will work as soon as ChatGPT Desktop supports it.

### Authentication with ChatGPT

If the buyer agent has `API_KEY` set, all requests to the MCP SSE endpoint require an `X-API-Key` header. ChatGPT Desktop MCP clients pass headers from the config:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

Check whether your version of ChatGPT Desktop supports the `headers` field --- some early versions do not.

---

## Cursor

Cursor's AI assistant supports MCP tool connections. Configure the buyer agent in Cursor's settings.

### Configuration

Open Cursor settings (`Cmd+,` on macOS) and navigate to **Features > MCP**. Add a new server:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse"
    }
  }
}
```

Alternatively, create or edit `.cursor/mcp.json` at your project root:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "url": "http://localhost:8001/mcp/sse",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

The `.cursor/mcp.json` approach scopes the tool connection to your project, which is useful when working across multiple codebases with different buyer agent instances.

### Using Buyer Tools in Cursor

With the connection active, invoke buyer tools in Cursor's chat using natural language:

> "List all active campaigns and their pacing status."

Cursor will call `list_campaigns(status="ACTIVE")` and `check_pacing()` for each result. This is particularly useful when reviewing campaign performance while editing related configuration or code.

---

## Windsurf

Windsurf (Codeium's AI IDE) supports MCP via its Cascade AI feature.

### Configuration

Create or edit the Windsurf MCP configuration file. On macOS:

```
~/.codeium/windsurf/mcp_config.json
```

Add the buyer agent:

```json
{
  "mcpServers": {
    "ad-buyer-agent": {
      "serverUrl": "http://localhost:8001/mcp/sse"
    }
  }
}
```

!!! note "Windsurf Configuration Key"
    Windsurf uses `serverUrl` rather than `url`. Check the [Windsurf MCP documentation](https://docs.codeium.com) for the current schema --- it may differ between versions.

Restart Windsurf after editing the config.

---

## Generic MCP Client Configuration

Any MCP-compatible client can connect to the buyer agent's SSE endpoint. The information you need:

| Property | Value |
|----------|-------|
| **Transport** | Server-Sent Events (SSE) |
| **Endpoint** | `http://localhost:8001/mcp/sse` |
| **Server name** | `ad-buyer-agent` |
| **Tool discovery** | Automatic via `initialize` + `tools/list` |
| **Auth header** | `X-API-Key: <your-key>` (only if `API_KEY` is set) |
| **Protocol version** | MCP 1.0 (FastMCP) |

### SSE Connection Flow

1. Client opens a GET request to `/mcp/sse` (SSE stream)
2. Server sends the `initialize` response with server info and capabilities
3. Client sends `tools/list` to enumerate available tools and their JSON schemas
4. Client calls tools via the SSE stream using MCP tool call messages
5. Server streams tool results back

This is the same flow used by Claude Desktop, Cursor, and Windsurf. Any client implementing MCP over SSE transport will work.

### Tool Discovery via REST (SimpleMCP clients)

Some lightweight clients do not implement full SSE transport. For these, the buyer agent's seller-side REST fallback pattern applies: `GET /mcp/tools` is not exposed by default on the buyer's MCP server (it uses FastMCP SSE only). Use the full SSE endpoint for tool discovery.

---

## Multi-Client Considerations

### Concurrent Access

The buyer agent's MCP tools are stateless: each tool call reads from or writes to the SQLite database and returns a result. Multiple clients can connect simultaneously without coordination issues.

**Concurrent read operations** (e.g., two clients calling `list_campaigns` at the same time) are safe.

**Concurrent write operations** (e.g., two clients calling `approve_or_reject` on the same approval request simultaneously) rely on SQLite's built-in serialization. The second write will see the state left by the first. Approval tools check current status before acting and return an error if the request is already decided --- so concurrent approval attempts will not double-apply.

### Session Management

The buyer MCP server is stateless at the MCP layer. Each tool call is independent. There are no per-client sessions to manage. Clients can connect, disconnect, and reconnect at any time without server-side cleanup.

This means:

- Clients do not need to "log out"
- A client crash does not leave orphaned state on the server
- The same tool can be called from multiple clients in the same time window

### Shared State Awareness

All MCP clients share the same underlying database. A campaign created by one client is immediately visible to all other connected clients. This is intentional --- the buyer agent is a single system, not per-user isolated instances.

If you run multiple buyer agent instances (e.g., one per advertiser), run separate server processes on different ports, each with its own `DATABASE_URL`.

---

## Client Comparison

| | Claude Desktop | ChatGPT Desktop | Cursor | Windsurf |
|---|---|---|---|---|
| **MCP Support** | Native, stable | Rolling out | Stable | Stable |
| **Config format** | `command` or `url` | `url` | `url` | `serverUrl` |
| **Auth header** | Supported | Version-dependent | Supported | Version-dependent |
| **Best for** | Conversational campaign management | General assistant workflows | Campaign work alongside code edits | Campaign work alongside code edits |
| **Tool discovery** | Automatic | Automatic | Automatic | Automatic |
| **Streaming results** | Yes | Yes | Yes | Yes |

---

## API Key Management for Multiple Clients

The buyer agent uses a single inbound API key (`API_KEY` environment variable) to authenticate all incoming requests, including MCP connections. There is no per-client key management at this time.

### Recommended Setup for Multi-Client Access

**Development (no authentication):**

Leave `API_KEY` empty in your `.env` file. All clients connect without any credential:

```bash
API_KEY=
```

**Production (shared key):**

Set a strong API key and distribute it to all clients that need access:

```bash
API_KEY=your-strong-shared-key
```

Configure the key in each client's header configuration (see per-client sections above).

**If you need per-client access control**, run a separate buyer agent instance per client with its own `API_KEY` and `DATABASE_URL`. Each instance is a fully independent buyer system.

### Seller API Keys

Seller API keys (used by the buyer to authenticate with seller agents) are stored separately in `~/.ad_buyer/seller_keys.json`. These are not related to the inbound `API_KEY` and are managed through the `create_api_key`, `list_api_keys`, and `revoke_api_key` MCP tools:

```
"Add a seller API key for http://seller.example.com with key abc123"
```

The client calls `create_api_key(seller_url="http://seller.example.com", api_key="abc123")`. Full key values are stored locally on the buyer's server and are never returned to MCP clients --- only masked values are shown.

---

## Security Considerations

### Exposing the Buyer Agent Externally

The buyer agent is designed for local or private-network use. If you expose it on a public or shared network:

1. **Set a strong `API_KEY`** --- all MCP connections will require the `X-API-Key` header.
2. **Use HTTPS** --- run the buyer behind a reverse proxy (nginx, Caddy) with TLS. MCP SSE over plain HTTP exposes tool calls and results in cleartext.
3. **Restrict CORS** --- set `CORS_ALLOWED_ORIGINS` to only the origins that need browser access.

### What MCP Clients Can Do

Any client with a valid API key can:

- Read all campaigns, deals, pacing data, and config (excluding secrets)
- Approve or reject pending approval requests
- Create and revoke seller API keys
- Create templates and instantiate deals from them

This is significant access. Treat the `API_KEY` accordingly --- do not share it in public config files or version control.

### What MCP Clients Cannot Do

- Read full API key values (only masked versions are exposed)
- Read the `ANTHROPIC_API_KEY` or other internal secrets
- Access the database directly (all access is through typed tool interfaces)
- Start or stop booking workflows (this is REST-only, not exposed via MCP tools)

### Approval Tool Caution

The `approve_or_reject` tool permanently changes approval state. Ensure that only trusted clients have access to a buyer agent instance that manages real campaigns.

---

## Troubleshooting

### Tools Not Appearing in Client

1. Verify the server is running: `curl http://localhost:8001/health`
2. Check that the SSE endpoint is reachable: `curl http://localhost:8001/mcp/sse` (should return an SSE stream, not an error)
3. Restart the client after editing configuration
4. Check client logs for connection errors

### Authentication Failures

If you see 401 errors:

1. Confirm `API_KEY` is set in the server's environment
2. Confirm the client is sending the `X-API-Key` header with the correct value
3. If developing locally, set `API_KEY=` (empty) to disable authentication

### Tool Call Errors

Tool errors are returned as JSON with an `"error"` key. Common causes:

| Error | Cause | Fix |
|-------|-------|-----|
| `"Campaign not found: ..."` | Invalid campaign ID | Use `list_campaigns` to get valid IDs |
| `"Approval request already decided"` | Request was already approved/rejected | Check current status with `list_pending_approvals` |
| `"template_type is required"` | Missing argument | Pass `template_type` as `"deal"` or `"supply_path"` |
| `"setup_complete": false` | Missing configuration | Check `get_setup_status` output for which checks failed |

### Setup Status Check

Use `get_setup_status` as your first diagnostic step from any MCP client:

```
"Check the buyer agent setup status and tell me what needs to be configured."
```

This returns the state of seller endpoints, database connectivity, API key configuration, and LLM configuration.

---

## Related

- [Quickstart](getting-started/quickstart.md) --- install and run the buyer agent
- [Configuration](guides/configuration.md) --- all environment variables including `API_KEY`
- [MCP Client (Seller Communication)](api/mcp-client.md) --- how the buyer calls seller agents via MCP
- [Authentication](api/authentication.md) --- API key setup for inbound requests
- [MCP Protocol Specification](https://modelcontextprotocol.io) --- official MCP documentation
