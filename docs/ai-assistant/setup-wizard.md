# Setup Wizard

The buyer agent includes a two-phase, 8-step setup wizard for guided configuration. It is accessible via MCP tools, so you can walk through setup conversationally with your AI assistant.

## Two Phases

Configuration is split by audience:

- **Developer Phase (steps 1–3)** --- Infrastructure decisions. Typically run once by the person deploying the buyer agent. Best done with Claude Code or a terminal.
- **Business Phase (steps 4–8)** --- Buyer identity and campaign preferences. Run by the media buying team. Best done with Claude Desktop.

The wizard transitions automatically from the Developer phase to the Business phase once steps 1–3 are done or skipped.

## Steps

### Developer Phase

| Step | Title | What it configures |
|------|-------|--------------------|
| 1 | Deploy & Environment | Deployment target (`local`, `docker`, `cloud`), API key, storage backend, database URL, environment name |
| 2 | Seller Connections | Seller agent MCP/A2A endpoint URLs; connectivity is tested per seller |
| 3 | Generate Operator Credentials | Creates an operator API key for MCP clients and outputs the Claude Desktop config snippet |

### Business Phase

| Step | Title | What it configures |
|------|-------|--------------------|
| 4 | Buyer Identity | Agency name, agency ID, DSP seat ID, seat name, holding company |
| 5 | Deal Preferences | Default deal types (`PD`, `PA`), max CPM threshold, preferred media types |
| 6 | Campaign Defaults | Default currency, pacing strategy (`even`/`front-loaded`/`back-loaded`), default flight duration |
| 7 | Approval Gates | Auto-approve threshold (default $5,000), require-approval threshold (default $50,000), escalation email |
| 8 | Review & Launch | Reviews all configured settings, runs health check, confirms ready — cannot be skipped |

## Using the Wizard via MCP

Start by asking your AI assistant to run the wizard:

> "Run the setup wizard and show me what's left to do."

This calls `run_setup_wizard`, which auto-detects already-configured steps and returns the full state.

To work through steps individually:

```
# See details for a specific step
get_wizard_step(step_number=4)

# Complete a step with your values
complete_wizard_step(
    step_number=4,
    config='{"agency_name": "Acme Media", "seat_id": "ttd-abc123"}'
)

# Skip a step and accept defaults
skip_wizard_step(step_number=5)
```

## Auto-Detection

When `run_setup_wizard` is called, the wizard inspects existing environment and configuration:

- **Step 1 auto-detected** if `API_KEY` is set in the environment
- **Step 2 auto-detected** if `SELLER_ENDPOINTS` is configured

Steps 3–8 require explicit completion because they involve generating credentials, identity data, and business policy decisions that cannot be inferred from environment variables alone.

Auto-detected steps appear with status `auto_detected` rather than `completed`. They count toward overall progress.

## Skipping Steps

Steps 1–7 can all be skipped. Skipping applies the step's defaults:

| Step | Key defaults applied on skip |
|------|------------------------------|
| 1 | `deployment_target=local`, `storage_backend=sqlite` |
| 2 | `seller_endpoints=[]` (no sellers configured) |
| 4 | `agency_name="My Agency"` |
| 5 | `default_deal_types=["PD", "PA"]`, `max_cpm_threshold=50.0` |
| 6 | `currency=USD`, `pacing_strategy=even`, `flight_duration=30 days` |
| 7 | `auto_approve_below=5000`, `require_approval_above=50000` |

**Step 8 cannot be skipped.** Review & Launch requires an explicit completion call, confirming all settings look correct before the buyer agent begins operation.

!!! tip "Fast path for development"
    Skip steps 1–7 to get a running system with all defaults, then complete step 8. You can revisit individual steps later once the system is live.

## Wizard State

The wizard tracks state in memory across MCP calls within a session. Progress is not automatically persisted between server restarts — re-run `run_setup_wizard` at the start of each session to restore state from existing configuration via auto-detection.
