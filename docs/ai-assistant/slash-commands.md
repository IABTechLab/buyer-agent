# Slash Commands Reference

The buyer agent registers **10 MCP prompts** that appear as `/` commands in Claude Desktop and Claude on the web. Each command injects a structured prompt that guides Claude to call the right tools for a specific workflow.

## Platform Support

| Platform | `/` commands visible? | How to trigger |
|----------|----------------------|----------------|
| Claude Desktop | Yes | Type `/` to see the menu, or type the command name |
| Claude Web | Yes | Same as Desktop — integrations sync across both |
| ChatGPT | No | Prompts are not surfaced; use natural language instead (tools still work) |
| Cursor / IDEs | Varies | Depends on IDE MCP support |

---

## Quick Reference

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

---

## Command Details

### `/setup` --- First-Time Guided Wizard

Launches the 8-step setup wizard, auto-detects completed steps, and walks you through configuring everything that is incomplete --- one step at a time.

**Tools it uses:** `run_setup_wizard`, `get_wizard_step`, `complete_wizard_step`, `skip_wizard_step`, `get_setup_status`

See [Setup Wizard](setup-wizard.md) for full details on each step.

---

### `/status` --- Configuration and Health Overview

Shows a complete status overview: setup completeness, system health, seller connectivity, database status, and any issues needing attention.

**Tools it uses:** `get_setup_status`, `health_check`, `get_config`

!!! tip
    Run `/status` after deployment to verify everything is connected before handing off to the media buying team.

---

### `/campaigns` --- Campaign Portfolio Overview

Lists all campaigns with budget pacing status, highlights campaigns that are behind or ahead on pacing, and flags any that need attention.

**Tools it uses:** `list_campaigns`, `review_budgets`, `check_pacing`

---

### `/deals` --- Deal Portfolio Dashboard

Shows the complete deal portfolio: active deals, deals by type and status, top sellers, expiring deals, and total portfolio value.

**Tools it uses:** `get_portfolio_summary`, `list_deals`, `list_active_negotiations`

---

### `/discover` --- Find and Compare Sellers

Searches the IAB AAMP registry for seller agents, shows their capabilities, and offers to fetch media kits for comparison.

**Tools it uses:** `discover_sellers`, `get_seller_media_kit`, `compare_sellers`

!!! note
    Discovery queries the IAB Agent-to-Agent Marketplace Protocol (AAMP) registry. Sellers must be registered to appear in results.

---

### `/negotiate` --- Negotiation Status and Actions

Shows all active negotiations with round counts, current offer positions, and next actions. Offers to start new negotiations or respond to existing ones.

**Tools it uses:** `list_active_negotiations`, `get_negotiation_status`, `start_negotiation`, `discover_sellers`

---

### `/orders` --- Active Orders and Execution Status

Lists all orders with their current status, highlights orders needing action (pending approval, stuck transitions), and shows order pipeline health.

**Tools it uses:** `list_orders`, `get_order_status`, `list_pending_approvals`

---

### `/approvals` --- Pending Approvals Queue

Shows everything waiting for your decision: pending deal approvals, campaign approvals, and budget change requests. Most urgent first.

**Tools it uses:** `list_pending_approvals`, `approve_or_reject`, `list_orders`

---

### `/configure` --- Settings, Templates, and SSP Connectors

Shows the current buyer configuration, available templates (deal and supply path), and SSP connector status. Allows you to create templates or configure connectors.

**Tools it uses:** `get_config`, `list_templates`, `list_ssp_connectors`, `list_api_keys`, `create_template`, `test_ssp_connection`

---

### `/help` --- What Can This Agent Do?

Lists all available slash commands and tool categories with brief descriptions. Orients new users.

**Tools it uses:** None --- Claude responds from its knowledge of the available prompts and tools.

---

## Comparison with Seller Agent

The buyer and seller agents expose parallel slash commands tailored to their respective roles:

| Seller Command | Buyer Command | Relationship |
|---------------|--------------|--------------|
| `/setup` | `/setup` | Both run 8-step wizards with role-specific steps |
| `/status` | `/status` | Same pattern --- configuration and health |
| `/inventory` | `/campaigns` | Sellers manage inventory; buyers manage campaigns |
| `/deals` | `/deals` | Sellers generate deals; buyers manage deal portfolios |
| `/queue` | `/approvals` | Seller's inbound queue; buyer's approval queue |
| `/new-deal` | `/negotiate` | Sellers create deals; buyers negotiate for deals |
| `/configure` | `/configure` | Both cover settings; buyer adds templates and SSPs |
| `/buyers` | `/discover` | Sellers see buyer activity; buyers discover sellers |
| `/help` | `/help` | Same pattern |
| *(none)* | `/orders` | Buyer-specific: order execution and status tracking |
