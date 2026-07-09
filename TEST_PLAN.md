# Buyer-Agent — End-to-End Test Plan (AAMP v2.1)

Baseline: v2.0 (commit `cc9c4d77`, Apr 13 2026) → v2.1 cut-off Jul 1 2026.
Scope: **buyer-agent only.** Covers REST (11 endpoints), MCP (40 tools), AgentCore, CLI, and the v2.1 feature blocks (SGP vendor-approval gate, CPM hallucination fix, pluggable storage, Bedrock AgentCore, AudiencePlan wire-format spec, Streamable MCP smoke tests).

---

## 1. How to run

1. **Baseline test suite (do this first):**
   ```bash
   source venv/bin/activate  # arm64 pyenv 3.12.11 venv already present in repo root
   export PYTHONPATH="$PWD/src:$PYTHONPATH"
   python -m pytest tests/unit/ -q            # 3178 tests
   python -m pytest tests/integration/ -q     # 141 tests (no server needed)
   ```
   Unlike seller-agent, no arm64/uv interpreter workaround was needed here — the repo's own `venv/` (pyenv 3.12.11, arm64) installs and runs cleanly with `pip install -e .`. crewai 1.14.6 resolves fine.

2. **Start the server (local, credential-free for read paths; needs `ANTHROPIC_API_KEY` for crew-backed flows):**
   ```bash
   uvicorn ad_buyer.interfaces.api.main:app --port 8000
   ```
   MCP is served at `http://localhost:8000/mcp` (Streamable HTTP) and `…/mcp-sse/sse` (legacy SSE).

3. **Smoke checks:**
   - `GET /health`
   - MCP `tools/list` at `/mcp` (see Test.md §0)
   - `python -m pytest tests/smoke/ -q` (server must already be running; see bug #6 below re: the legacy-SSE smoke suite's wrong default URL)

### Prerequisites / config
| Var | Value for local testing | Notes |
|-----|-------------------------|-------|
| `ANTHROPIC_API_KEY` | required for any crew-backed flow (`POST /bookings`) | Not needed for `/health`, `/events`, `/api/v1/buyer/orders`, MCP read tools |
| `MANAGER_LLM_MODEL` | **override to** `anthropic/claude-sonnet-4-5-20250929` | See bug #1 — the shipped default is broken |
| `STORAGE_TYPE` | `sqlite` (default) | `redis` / `hybrid` need external infra — see §1a |
| `DATABASE_URL` | `sqlite:///./ad_buyer.db` (default) | |
| `SGP_API_KEY` | empty = inert (default) | Set + `SGP_ENFORCE=true` to exercise the gate |
| `SGP_ENFORCE` | `false` (default) | See bug #4 — the gated flow has no live entrypoint anyway |
| `OPENDIRECT_BASE_URL` | `http://localhost:3000` (default) | No local mock server was available in this environment; `/products/search` and channel-crew inventory calls fail with a connection error (expected, not a product bug) unless a real OpenDirect/seller endpoint is reachable |
| `IAB_SERVER_URL` | IAB-hosted default, or point at a local seller-agent (`uvicorn ad_seller...:app --port 8010`) for cross-repo testing | |

### 1a. Storage backends
- **SQLite (default):** live-tested — `DealStore`/`OrderStore` initialize and persist correctly during the live `/bookings` run (§2b).
- **Redis:** requires a running Redis instance (`REDIS_URL=redis://localhost:6379/0`, `STORAGE_TYPE=redis`). Not available in this sandboxed environment — **skip-noted**, not testable here. `storage/redis_backend.py` reviewed statically; lazy-imported so it doesn't affect the sqlite path.
- **Postgres+Redis hybrid:** requires both a Postgres and a Redis instance (`STORAGE_TYPE=hybrid`). **Skip-noted** for the same reason. `storage/factory.py:66-81` correctly validates both URLs are present and raises `ValueError` with an actionable message when they're not (code-reviewed, not exercised).

### Baseline run — observed result (2026-07-09)
```bash
python -m pytest tests/unit/ -q         # 3175 passed, 1 failed, 2 skipped, 200.66s
python -m pytest tests/integration/ -q  # 132 passed, 9 skipped, 34.89s
```
- **1 unit failure — environment-only, not a product bug:** `tests/unit/agentcore/test_agentcore_memory_patch.py::TestMemoryIntegration::test_crew_with_memory_true` → `ImportError: AWS Bedrock native provider not available, to install: uv add "crewai[bedrock]"`. `boto3` is not installed in this venv even though `pyproject.toml` declares the `bedrock` extra; the crewai package itself lazy-imports `boto3`/`botocore` and fails without it. Fix: `pip install boto3` (or reinstall with `pip install -e ".[bedrock]"` from a network that has the wheel).
- **Cross-repo AudiencePlan round-trip test — confirmed FIXED (unlike the analogous seller-side bug):** `tests/integration/test_path_a_audience_e2e.py::TestCrossRepoAudiencePlanJSONRoundTrip` now cleanly `pytest.skip()`s when the sibling `ad_seller_system` checkout isn't present (commit `90764ab`, PR #92), instead of raising `RuntimeError` as the seller-side analog still does. Verified live:
  ```bash
  python -m pytest tests/integration/test_path_a_audience_e2e.py -k CrossRepo -q     # → 1 skipped, clean message
  AD_SELLER_SRC_PATH=/Users/kishoren/IABTechLab/seller-agent/src \
    python -m pytest tests/integration/test_path_a_audience_e2e.py -k CrossRepo -q   # → 1 passed
  ```
- **Coverage caveat:** green unit/integration tests do **not** exercise the live runtime bugs below (broken default LLM model, `/products/search` 500, duplicate route mounting). Run the manual flows in §3 / Test.md.

---

## 2. 🐞 Bugs found (code-verified, with live confirmation where noted)

| # | Sev | Bug | Location | Manifestation |
|---|-----|-----|----------|---------------|
| 1 | **HIGH — live-confirmed** | `MANAGER_LLM_MODEL` default is still the broken/retired model. The fix commit (`86c18a0`, "Fix MANAGER_LLM_MODEL default: opus-4-20250514 → sonnet-4-5-20250929") only edited `.env.example`; it never touched the actual `Settings` class default. | `src/ad_buyer/config/settings.py:66` (`manager_llm_model: str = "anthropic/claude-opus-4-20250514"`); consumed at `agents/level1/portfolio_manager.py:57` | Any deployment that doesn't explicitly override `MANAGER_LLM_MODEL` (including this checkout's own committed `.env`, which still has the stale value) gets the broken model. **Reproduced live:** `POST /bookings` → job errors with `"Budget allocation failed: Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: claude-opus-4-20250514'}}"`. Overriding `MANAGER_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929` at server start fixes it (confirmed — see §2b). |
| 2 | HIGH | `POST /products/search` crashes under uvicorn's default event loop. `async_utils.run_async()` calls `nest_asyncio.apply(loop)` on the *currently running* loop to support "FastAPI ... event loops" per its own docstring — but `nest_asyncio` only supports patching pure-Python `asyncio` loops, not `uvloop`, which is uvicorn's default loop when the `uvloop` package is installed (it is, transitively, via `uvicorn[standard]`/crewai deps). | `src/ad_buyer/async_utils.py:41`; triggered from `src/ad_buyer/interfaces/api/main.py:493-509` (`search_products`, the only endpoint that calls a CrewAI `BaseTool._run()` synchronously from the request-handling coroutine) | **Reproduced live:** `POST /products/search` → HTTP 500 "Internal Server Error"; server log shows `ValueError: Can't patch loop of type <class 'uvloop.Loop'>` plus `RuntimeWarning: coroutine 'ProductSearchTool._arun' was never awaited`. This is the only REST endpoint that calls `run_async()` directly from an async handler — everywhere else (`/bookings`, `approve-all`) it's insulated inside `asyncio.to_thread(flow.kickoff)`, which runs on a worker thread with a plain asyncio loop, not uvloop. |
| 3 | MED | `GET/POST /api/v1/buyer/orders*` routes are mounted **twice**: once unconditionally at import time (`_mount_order_router()`, called at module load) and again inside the ASGI `lifespan()` context manager at server startup. | `src/ad_buyer/interfaces/api/main.py:78-90` (lifespan) and `:118-126` (`_mount_order_router()` + its module-level call) | **Confirmed via live route introspection** (`TestClient` + lifespan): `app.routes` contains two separate `APIRouter` registrations for both `/api/v1/buyer/orders` and `/api/v1/buyer/orders/{order_id}/audit`. Harmless today (Starlette matches the first-registered route; both are idempotent GETs backed by the same `OrderStore`), but it's dead-code duplication and a correctness risk if a mutating endpoint is ever added to this router (double side effects). |
| 4 | MED — **reachability gap, not a functional bug** | The IAB Diligence Platform (SGP) vendor-approval gate — this release's flagship feature — has **no live entrypoint** in the running server. `SGPClient`/`sgp_enforce` wiring lives entirely inside `BuyerDealFlow` (`flows/buyer_deal_flow.py`), and `BuyerDealFlow`/`run_buyer_deal_flow` has **zero call sites** anywhere in `src/ad_buyer/interfaces/` (REST `main.py`, `mcp_server.py`, `cli/main.py`) or `examples/`. The 40 MCP tools enumerated live (§4) include nothing SGP-related, and the REST surface's only crew flow (`DealBookingFlow`, used by `POST /bookings`) never constructs `DiscoverInventoryTool`/`RequestDealTool` with `sgp_client`/`sgp_enforce` at all — those only get SGP wiring inside `BuyerDealFlow`. | `src/ad_buyer/flows/buyer_deal_flow.py:204-239`; confirmed by `grep -rln "run_buyer_deal_flow\|BuyerDealFlow(" src/ad_buyer/` returning only the flow's own module | The gate itself (`_check_sgp_approval`, `_apply_enforcement`, `SGPClient.check_approvals`) is correctly implemented and unit-tested (`tests/unit/test_sgp_gate.py`, `test_sgp_client.py`) and is exercised end-to-end by `examples/dsp_deal_discovery.py` calling `DiscoverInventoryTool`/`RequestDealTool` **directly** (bypassing `BuyerDealFlow`'s `Flow.kickoff()` machinery entirely). There is currently no REST/MCP/CLI path that drives `BuyerDealFlow.kickoff()` in production code. |
| 5 | LOW | `Flow.kickoff()`'s internal `ThreadPoolExecutor` occasionally doesn't join within its timeout after a `/bookings` run completes. | Observed at runtime, not a specific product line — crewai's own `crewai/flow/flow.py` internals via the `asyncio.to_thread(flow.kickoff)` wrapper added in PR #105 | Server log: `RuntimeWarning: The executor did not finishing joining its threads within 300 seconds.` Request still completed successfully (`status: completed`, correct `budget_allocations`); this is a shutdown-hygiene warning, not a functional failure. Likely a residual effect of running crewai's own executor a second layer deep inside `asyncio.to_thread`. |
| 6 | LOW — test-harness only | `tests/smoke/test_mcp_e2e.py`'s default `BUYER_MCP_URL` (`http://127.0.0.1:8000/mcp/sse/sse`) does not match where legacy SSE is actually mounted (`mount_mcp()` in `mcp_server.py` mounts it at `/mcp-sse`, i.e. `/mcp-sse/sse`). | `tests/smoke/test_mcp_e2e.py:37` vs `src/ad_buyer/interfaces/mcp_server.py` `mount_mcp()` | **Re-confirmed live (2nd pass):** default URL → self-skips (`1 skipped, 40 deselected`); `curl http://localhost:8000/mcp/sse/sse` → 404; `curl --max-time 3 http://localhost:8000/mcp-sse/sse` → 200 (stream stays open). With the corrected URL (`BUYER_MCP_URL=http://127.0.0.1:8000/mcp-sse/sse`), `test_mcp_e2e.py -k get_setup_status` still did not complete within 45s and had to be force-timed-out — reproduced identically on a second, independent run. Not root-caused further (likely an MCP SSE client/session handshake issue in the smoke test itself, since the raw HTTP GET is reachable in <3s). The **Streamable HTTP** smoke suite (`test_mcp_streamable.py`) has the correct default and passed **13/13 live on both runs**. |
| 8 | **HIGH — newly found, undermines the "horizontal scaling" claim** | **`jobs` (booking status) is a bare in-memory `dict`, never read back from persistence.** `jobs: dict[str, dict[str, Any]] = {}` (`main.py:161`, comment: "In-memory job storage (use Redis/DB in production)"). `_persist_job()` (`main.py:213`) does a write-only "best-effort dual-write" of each job into `DealStore`'s `jobs` sqlite table — and `DealStore.get_job()` / a `SELECT * FROM jobs ...` list query **already exist** (`storage/deal_store.py:739,783`) — but `get_booking_status` (`main.py:364-367`, `if job_id not in jobs: raise 404`) and `list_bookings` (`main.py:468-490`, iterates `jobs.items()` only) never call them. | `src/ad_buyer/interfaces/api/main.py:161,213,363-377,467-490` vs `src/ad_buyer/storage/deal_store.py:739,783` (read methods exist, unused) | **Reproduced live:** ran a `/bookings` job to `completed` on one server process; restarted the server (same `ad_buyer.db`); `sqlite3 ad_buyer.db "select id,status from jobs"` still shows the completed job row; but `GET /bookings/{that_job_id}` → **404 "Job not found"**, and `GET /bookings` → `{"jobs":[],"total":0}` — both on the exact same sqlite file. This isn't just a restart-hygiene bug: because `jobs` is a per-process Python dict, it **also breaks the multi-instance horizontal-scaling use case that pluggable storage (Redis/Postgres) was added for this release specifically to enable** — a `POST /bookings` landing on instance A and a `GET /bookings/{id}` poll landing on instance B behind a load balancer would 404 immediately, with zero code changes needed to reproduce it (no restart required, just two processes). The pluggable-storage work covers `DealStore`/`OrderStore` correctly (both bounced through the DB); the booking-job status store was not migrated onto the same abstraction. |
| 9 | LOW — resilience gap, not yet triggered by product code | **No client-side request timeout on any CrewAI `LLM()` construction.** All 10 agent factories (`agents/level1/portfolio_manager.py:56`, `level2/*.py`, `level3/*.py`) construct `LLM(model=settings.manager_llm_model, temperature=0.3)` with no `timeout`/`request_timeout` kwarg. | `src/ad_buyer/agents/level1/portfolio_manager.py:56-59` (and the 9 sibling agent files) | **Live-observed, not code-injected:** during this verification pass, a genuine transient loss of internet connectivity (external, not a code bug) caused an in-flight `POST /bookings` run to hang indefinitely at `progress:0.2` with near-zero CPU usage and zero new log lines for 12+ minutes — the underlying Anthropic HTTP call never times out or errors, so the job never reaches a terminal `failed` state and `flow.kickoff()`'s `asyncio.to_thread` worker is held forever. A fresh retry on a clean restart (after connectivity returned) completed normally in ~3.5 min with correct `budget_allocations`, confirming this is not a deterministic bug — but the missing timeout means any real-world network blip (not just the sandbox's) will silently strand a booking job with no error surfaced to the caller, only visible via a stuck `status:"running"` forever. Consider adding `timeout=`/`request_timeout=` to the `LLM()` constructors. |

**Note on live-run reproducibility:** the booking workflow (§2 flow 2 / Test.md §3) was run **four times** independently across this review and two subsequent verification passes — 3 of 4 attempts completed normally in 3.5–6 minutes with correct `budget_allocations`; the 1 anomaly was traced to an external network outage (bug #9 above), not a reproducible code defect.

**✅ Reviewed and live-confirmed clean (no bug):**
- **PR #105 fix (`Flow.kickoff()` blocking the event loop) — fully fixed and live-verified.** All four call sites the original bug report named are wrapped in `asyncio.to_thread()`: `api/main.py:454` (`approve_all_recommendations`), `api/main.py:593` (`_run_booking_flow` → `flow.kickoff`), `api/main.py:608` (auto-approve path), `flows/buyer_deal_flow.py:689` (`run_buyer_deal_flow`). **Live test:** while a real `POST /bookings` crew run was in flight (Anthropic LLM calls taking several minutes), five concurrent `GET /health` requests all returned in <1.3ms each — the event loop never stalled. This is a genuine fix, not just present-in-source.
- **DealBookingFlow silent-failure fix (ar-jbod, commit `cc0f0b0`) — live-confirmed.** `PortfolioCrew`'s budget task now declares `output_pydantic=BudgetAllocationOutput`, and `DealBookingFlow._extract_allocations()` reads `tasks_output[0]` instead of the crew's last-task `raw` output. **Live test:** `POST /bookings` → `GET /bookings/{id}` returned real, non-empty, correctly-typed `budget_allocations` (`branding: $30,000/60%`, `ctv: $20,000/40%`, with LLM-authored rationale text) — not the pre-fix empty-dict silent success. When the LLM call itself failed (bug #1, before the model override), `flow.state.errors` correctly surfaced the failure in the API response instead of masking it.
- **CPM hallucination fix (`d7b9572`/`9a3978f`) — code-verified.** `PricingCalculator.calculate()` returns `pricing_source=PricingSource.UNAVAILABLE` and all price fields `None` when `base_price is None` (`booking/pricing.py:139-150`) rather than substituting a hardcoded value. `RequestDealTool._create_deal_response()` returns `None` (surfaced as an explicit "No pricing available" error string) when `base_price` isn't numeric — no fabricated CPM path found. `DiscoverInventoryTool._format_results()` shows `"Pricing unavailable (rate on request)"` instead of `$0.00` when a product has no valid `base_price`.
- **`crew_memory_enabled` respects config, not hardcoded `True`.** All 10 agent factories (`agents/level1/*.py`, `level2/*.py`, `level3/*.py`) plus both crew factories read `memory=settings.crew_memory_enabled`; confirmed live via MCP `get_config` (`"crew_memory_enabled": true`, reflecting the actual setting, not a literal).
- **crewai pin** — `pyproject.toml:8` declares `"crewai[tools,anthropic,bedrock]>=1.14.4,<2.0.0"`; installed version in the repo's own `venv/` is `1.14.6` — in range.
- **Docker build fix** — `Dockerfile:13` copies `README.md` alongside `pyproject.toml` before `pip install -e .`; `pyproject.toml` pins `hatchling>=1.21` as the build backend.
- **Telemetry shutdown hang fix (`d673c61`)** — `_telemetry_shim.py` sets opt-out env vars before crewai/chromadb/posthog import; reviewed, not independently timed (would require a fresh CLI-process shutdown benchmark, out of scope for this pass).
- **AgentCore Bedrock Converse patches** — code-reviewed only (needs real AWS Bedrock creds + `boto3`, neither available here). `patches/crewai_bedrock_fix.py` implements the described orphaned-toolUse/toolResult sanitization (`_sanitize_tool_blocks`) monkey-patched onto `BedrockCompletion._handle_converse`; logic reads as sound (pairs toolUse/toolResult by `toolUseId` across adjacent assistant/user messages, strips unmatched blocks in both directions). 73 AgentCore-specific unit tests collected; all pass except the `boto3`-missing failure in bug-adjacent item above.
- **AudiencePlan wire-format spec** — `docs/api/audience_plan_wire_format.md` exists (414 lines, added in commit `e89a582`), matches what `deals_client.py`/`audience_plan.py`/`openrtb_builder.py` already implement.
- **Streamable MCP smoke tests** — `tests/smoke/test_mcp_streamable.py` (13 tests) passed live against `/mcp` with no server-side issues.
- **MIXPEEK_API_KEY / PR #78** — not merged. No reference to `MIXPEEK`/`mixpeek` anywhere in `src/`, `.env.example`, or `pyproject.toml`. Confirmed inert-by-absence (there's nothing to gate).

---

## 2b. 🔬 Live E2E run — session log (2026-07-09, port 8000, sqlite storage, real `ANTHROPIC_API_KEY`)

This environment had a **real, working Anthropic API key** available (unlike a pure-mock setup), so — unlike a static-review-only pass — several crew-backed flows were actually driven end-to-end with a live LLM.

1. `GET /health` → 200 immediately after `uvicorn` start.
2. MCP `tools/list` via a real `mcp` Python client (Streamable HTTP) → enumerated all 40 tools; called `get_setup_status`, `health_check`, `get_config` — all returned well-formed JSON.
3. `POST /products/search` (no filters) → **500** (bug #2 above).
4. `POST /bookings` (real campaign brief, `auto_approve:false`) with the **stock/default** config → job ended in `errors: ["Budget allocation failed: ... model: claude-opus-4-20250514"]` (bug #1, first reproduction).
5. Restarted the server with `MANAGER_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929` explicitly set → re-ran the same `POST /bookings` call. This time:
   - Multiple real CrewAI agent crews ran sequentially (portfolio manager → branding crew → CTV crew), each making real Anthropic tool-calling requests (visible in server log: `Anthropic: Successfully validated tool '...'`).
   - **While this ~6-minute LLM-backed run was in flight**, 5 concurrent `GET /health` calls all returned in under 1.3ms — confirms bug fix for PR #105 is real, not just source-level.
   - Channel crews' `search_advertising_products` tool calls failed with `"All connection attempts failed"` — **expected**, since no real OpenDirect/seller mock was running at `OPENDIRECT_BASE_URL=http://localhost:3000` in this sandbox. Handled gracefully (returned as tool-output text, not a crash).
   - Job finished `status: awaiting_approval`, `progress: 0.9`, with correctly-typed, non-empty `budget_allocations` for `branding` ($30,000/60%) and `ctv` ($20,000/40%) — confirms the ar-jbod DealBookingFlow fix live.
   - `POST /bookings/{id}/approve-all` → `{"status":"success","booked":0,"message":"No recommendations approved"}` (0 because no product recommendations existed, given the connection failures above) — no crash, correct handling of the empty case.
   - `GET /bookings/{id}` afterward → `status: completed`, `progress: 1.0`.
   - `GET /events?limit=10` → real `campaign.created` and `budget.allocated` events present with correct payloads, confirming the event bus works end-to-end.
   - Server log showed one `RuntimeWarning: The executor did not finishing joining its threads within 300 seconds` after this run (bug #5) — non-fatal.
6. `GET /api/v1/buyer/orders` → 200, `{"orders":[],"count":0}` — confirmed reachable, and confirmed (via `TestClient` + lifespan route introspection) that both order-endpoints are double-mounted (bug #3).
7. Cross-repo AudiencePlan round-trip test (`test_path_a_audience_e2e.py::TestCrossRepoAudiencePlanJSONRoundTrip`) — skip-cleanly by default; passes when pointed at the real sibling `seller-agent/src` via `AD_SELLER_SRC_PATH` (confirmed, see §1).
8. `tests/smoke/test_mcp_streamable.py` → 13/13 passed against the live server. `tests/smoke/test_mcp_e2e.py` (legacy SSE) → all self-skip due to bug #6's wrong default URL.

**Not exercised live (documented, not fabricated):**
- SGP vendor-approval gate: no live entrypoint exists (bug #4) — verified at the unit-test level only (`test_sgp_gate.py`, `test_sgp_client.py` — both pass in the baseline run).
- Redis / Postgres+Redis storage backends: no local infra available — skip-noted (§1a).
- AgentCore `/invocations` (Bedrock): needs AWS Bedrock creds + `boto3` — skip-noted, static code review only.
- CLI `freewheel`-style auth flows: buyer-agent's CLI (`book`, `search`, `status`, `chat`, `init`) has no OAuth/PKCE flow to test (that's a seller-agent-only feature); not applicable here.
- PR #87 (Meta Ads) — confirmed **not merged** to `main` (`git log --oneline --all | grep -i meta` shows only commits on `feature/meta-integration`, a stray branch never merged). Out of scope, as expected.
- PR #78 (Mixpeek) — confirmed **not merged**; no `MIXPEEK` reference anywhere in `src/`.
- Bead `buyer-xa5` (SSP integration planning) and `buyer-te6b.4.4` (external optimization hooks) — planning-only; `tools/deal_library/connectors/{pubmatic,magnite,index_exchange}.py` exist as connector stubs (used by the `list_ssp_connectors`/`import_deals_ssp`/`test_ssp_connection` MCP tools), consistent with "planning, not full integration."

---

## 2c. 🔬 Independent re-verification pass (2026-07-09, second session)

Re-ran this entire plan end-to-end in a fresh session against the same checkout to confirm the findings above weren't one-off. Results:

- **Baseline suites re-confirmed identical:** `tests/unit/` → 3175 passed/1 failed(boto3)/2 skipped; `tests/integration/` → 132 passed/9 skipped.
- **Bug #1 (MANAGER_LLM_MODEL) re-confirmed:** `.env`/`settings.py:66` still ship the broken `opus-4-20250514` default; overriding to `sonnet-4-5-20250929` at server start fixes it (`get_config` reflected the override correctly).
- **Bug #2 (`/products/search` 500 under uvloop) re-confirmed** with an identical traceback (`async_utils.py:41`, `ValueError: Can't patch loop of type <class 'uvloop.Loop'>`).
- **Bug #3 (double-mounted `/api/v1/buyer/orders*`) re-confirmed** via live route introspection — 4 registrations for 2 unique paths.
- **Bug #6 (legacy SSE smoke suite wrong URL) re-confirmed**, including the corrected-URL hang (forced timeout after 45s on a second, independent attempt).
- **New — bug #9 (no LLM client timeout):** discovered when a real, transient loss of internet connectivity during this session caused an in-flight `/bookings` run to hang indefinitely (see bug #9 above). A same-payload retry after connectivity returned completed normally in ~3.5 min with correct `budget_allocations` — confirms PR #105's concurrency fix and the ar-jbod `DealBookingFlow` fix are both still solid; the hang was environmental, not a regression, but exposed the missing-timeout gap.
- **Cross-repo AudiencePlan test re-confirmed:** clean skip without `AD_SELLER_SRC_PATH`, passes with it pointed at the real sibling `seller-agent/src`.
- **SGP gate unit tests re-confirmed:** 39/39 pass (`test_sgp_gate.py` + `test_sgp_client.py`).
- **AgentCore unit tests re-confirmed:** 72 passed / 1 failed (same boto3-missing gap).
- **Streamable MCP smoke suite re-confirmed:** 13/13 passed.
- **Two documentation bugs found and fixed in Test.md** (not product bugs): §6's example payloads for `create_deal_manual` (missing required `display_name`/`seller_url`, and non-existent `deal_id`/`product_id` args) and `start_negotiation` (missing required `seller_url`/`product_id`, wrong `target_price` vs actual `initial_price`) were guesses that didn't match the real `inputSchema`; corrected against the live schema and re-verified working (`create_deal_manual` → `success:true`, new deal appears in `list_deals`). Also fixed §9's `sqlite3` example query, which referenced a non-existent `job_id` column — the real `jobs` table primary key column is `id`.

---

## 2d. 🔬 Full-coverage sweep — remaining untested surface closed out (2026-07-09, third session)

The passes above exercised the core flows and every previously-flagged bug, but had **not** individually called every one of the 40 MCP tools (only ~21 had been). This pass closed that gap:

- **All remaining 19 MCP tools called live, each reachable, none crash:** `run_setup_wizard`, `complete_wizard_step`, `skip_wizard_step`, `list_campaigns`, `review_budgets`, `get_campaign_status`, `check_pacing`, `import_deals_csv`, `create_template`, `instantiate_from_template`, `discover_sellers`, `get_seller_media_kit`, `compare_sellers`, `get_order_status`, `transition_order`, `approve_or_reject`, `create_api_key`, `revoke_api_key`, `import_deals_ssp`. All behave sensibly: empty-state reads return empty lists/zero counts; missing-id lookups return clean `{"error": "... not found"}` JSON (no 500s); unreachable seller URLs (`get_seller_media_kit`, `compare_sellers`) surface a clear connection-error message rather than crashing; `import_deals_csv`/`create_template`/`instantiate_from_template` validated correctly on bad input then succeeded on valid input (full round-trip: CSV import → 1 deal created; template created → instantiated into a new deal); `create_api_key`→`list_api_keys`→`revoke_api_key` full CRUD cycle confirmed. **40/40 MCP tools now individually exercised**, not just enumerated via `tools/list`.
- **Remaining REST gaps closed:** `GET /bookings` (list) and `GET /events/{event_id}` were run (both correct — empty-state responses on a fresh process, matching the in-memory-store finding below); `POST /bookings/{job_id}/approve` was attempted against a job ID from a since-restarted process → 404, which is exactly what **surfaced bug #8** below rather than being a gap.
- **New — bug #8 found in this pass (HIGH):** while chasing why `GET /bookings` returned empty on a freshly restarted server despite the sqlite `jobs` table clearly containing prior completed jobs, traced it to `jobs` being a bare in-memory `dict` in `main.py` that is never read back from `DealStore` (full detail in bug #8 above). This is the most significant finding of the three sessions — it means booking-job history/status does not survive a restart, and would not survive being load-balanced across multiple instances either, despite this release's pluggable storage backends being pitched specifically for multi-instance horizontal scaling.
- **All 11 REST endpoints and all 40 MCP tools have now been individually exercised live** against a running server at least once across these three sessions (AgentCore `/invocations` and Redis/Postgres+Redis backends remain skip-noted — they need infra not available in this sandbox, not because they were skipped by choice).

---

## 3. 🔄 Flows to test (E2E user journeys)

### P0 — core path
1. **Health / setup** — `GET /health` → MCP `get_setup_status` / `health_check` / `get_config` (verify `manager_llm_model` shown — should be overridden away from opus-4, see bug #1).
2. **Booking workflow (the flagship crew flow)** — `POST /bookings` (campaign brief) → poll `GET /bookings/{id}` until `awaiting_approval` or `completed` → `POST /bookings/{id}/approve` (selective) or `/approve-all` → `GET /bookings` (list). *(Bugs #1, #3, #5 live here; ar-jbod fix and PR #105 fix verified here too.)*
3. **Product search** — `POST /products/search` (blocked by bug #2 under default uvicorn/uvloop).
4. **Events** — `GET /events` / `GET /events/{id}` — confirm `campaign.created`, `budget.allocated`, `deal.booked` events appear as flows progress.
5. **Buyer order status/audit** — `GET /api/v1/buyer/orders` / `GET /api/v1/buyer/orders/{id}/audit` (populated by `OrderSyncService` pulling from a real seller; empty in a no-seller sandbox — verified reachable).

### P1 — MCP surface
6. **Setup & wizard** — `get_setup_status`, `run_setup_wizard`, `get_wizard_step`, `complete_wizard_step`, `skip_wizard_step`.
7. **Campaigns** — `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets`.
8. **Deal library** — `list_deals`, `search_deals`, `inspect_deal`, `import_deals_csv`, `create_deal_manual`, `list_templates`, `create_template`, `instantiate_from_template`.
9. **Negotiation** — `start_negotiation`, `get_negotiation_status`, `list_active_negotiations`.
10. **Orders** — `list_orders`, `get_order_status`, `transition_order`.
11. **Approvals** — `list_pending_approvals`, `approve_or_reject`.
12. **Auth** — `list_api_keys`, `create_api_key`, `revoke_api_key`.
13. **Reporting** — `get_deal_performance`, `get_campaign_report`, `get_pacing_report`.
14. **SSP connectors** — `list_ssp_connectors`, `import_deals_ssp`, `test_ssp_connection`.
15. **Seller discovery (A2A-adjacent)** — `discover_sellers`, `get_seller_media_kit`, `compare_sellers`.
16. **Portfolio** — `get_portfolio_summary`.

### P2 — v2.1 feature-specific (needs real infra / not reachable live here)
17. **SGP vendor approval gate** — unit-level only (bug #4): `pytest tests/unit/test_sgp_gate.py test_sgp_client.py -v`. To exercise live, run `examples/dsp_deal_discovery.py` against a real seller MCP/A2A endpoint with `SGP_API_KEY` + `SGP_ENFORCE=true` set.
18. **Storage backends** — sqlite (live-tested), redis/hybrid (skip-noted, §1a).
19. **AgentCore** — `POST /invocations` on the separate Bedrock runtime (`interfaces/agentcore/http_main.py`, needs `bedrock-agentcore` + AWS creds) — skip-noted.
20. **Cross-repo AudiencePlan round-trip** — `AD_SELLER_SRC_PATH=<seller>/src pytest tests/integration/test_path_a_audience_e2e.py -k CrossRepo` (live-tested, passes).

---

## 4. 🌐 API surface to test

### REST — 11 endpoints (4 tag groups + 1 sub-router)
- **Health:** `GET /health`
- **Bookings:** `POST /bookings`, `GET /bookings/{job_id}`, `POST /bookings/{job_id}/approve`, `POST /bookings/{job_id}/approve-all`, `GET /bookings`
- **Products:** `POST /products/search`
- **Events:** `GET /events`, `GET /events/{event_id}`
- **Buyer Orders** (sub-router, mounted twice — bug #3): `GET /api/v1/buyer/orders`, `GET /api/v1/buyer/orders/{order_id}/audit`

Auth: `X-API-Key` header, enforced by middleware only when `settings.api_key` is non-empty (dev-mode bypass by default — confirmed by reading `api_key_auth_middleware`, `main.py:132-157`). No header-as-query-param bug here (unlike seller-agent) — `request.headers.get("X-API-Key", "")` is read correctly from the actual header.

### MCP — 40 tools (`/mcp` Streamable HTTP + `/mcp-sse` legacy SSE)
Setup(6): `get_setup_status`, `health_check`, `get_config`, `run_setup_wizard`, `get_wizard_step`, `complete_wizard_step`, `skip_wizard_step` · Campaigns(4): `list_campaigns`, `get_campaign_status`, `check_pacing`, `review_budgets` · Deal library(8): `list_deals`, `search_deals`, `inspect_deal`, `import_deals_csv`, `create_deal_manual`, `list_templates`, `create_template`, `instantiate_from_template` · Seller discovery(3): `discover_sellers`, `get_seller_media_kit`, `compare_sellers` · Negotiation(3): `start_negotiation`, `get_negotiation_status`, `list_active_negotiations` · Orders(3): `list_orders`, `get_order_status`, `transition_order` · Approvals(2): `list_pending_approvals`, `approve_or_reject` · Auth(3): `list_api_keys`, `create_api_key`, `revoke_api_key` · Reporting(3): `get_deal_performance`, `get_campaign_report`, `get_pacing_report` · SSP(3): `list_ssp_connectors`, `import_deals_ssp`, `test_ssp_connection` · Portfolio(1): `get_portfolio_summary`.

(40 counted directly from `@mcp.tool()` decorators in `interfaces/mcp_server.py`; the module also defines 9 `@mcp.prompt()` templates — `setup_prompt`, `status_prompt`, `campaigns_prompt`, `deals_prompt`, `discover_prompt`, `negotiate_prompt`, `orders_prompt`, `approvals_prompt`, `help_prompt` — not counted as tools.)

### Other interfaces
- **AgentCore (Bedrock):** `POST /invocations` on a separate `BedrockAgentCoreApp` (`interfaces/agentcore/http_main.py`, port 8080) — requires AWS Bedrock, **skipped**, not testable against :8000.
- **CLI:** `ad-buyer book`, `search`, `status`, `chat`, `init` (`interfaces/cli/main.py`).
- **Examples:** `examples/dsp_deal_discovery.py` is the only place `DiscoverInventoryTool`/`RequestDealTool`/SGP gate are exercised outside unit tests (see bug #4).

---

## 5. Quick pass/fail checklist
- [x] `pytest tests/unit/` green modulo the boto3-only failure (3175/3176 effective)
- [x] `pytest tests/integration/` green (132/141, 9 skip = AWS/schema-drift/etc.)
- [x] `/health` responds 200
- [x] MCP `tools/list` enumerates 40 tools; **all 40 individually called live** (§2d), not just enumerated
- [x] All 11 REST endpoints individually called live at least once
- [ ] `POST /products/search` — **fails (bug #2)**
- [x] `POST /bookings` → poll → `approve-all` → `completed`, with real `budget_allocations` *(requires `MANAGER_LLM_MODEL` override — bug #1; run successfully 3 of 4 attempts, 1 network-related hang — bug #9)*
- [x] Concurrency: `/health` stays responsive during an in-flight `/bookings` crew run *(PR #105 verified)*
- [x] `GET /events` shows `campaign.created`/`budget.allocated`
- [x] `GET /api/v1/buyer/orders` reachable *(double-mounted — bug #3, cosmetic)*
- [ ] `GET /bookings` / `GET /bookings/{id}` survive a server restart — **fails (bug #8, HIGH — in-memory job store, not read from DealStore)**
- [x] Cross-repo AudiencePlan round-trip skips cleanly without sibling repo; passes with `AD_SELLER_SRC_PATH`
- [ ] SGP vendor-approval gate live E2E — **no live entrypoint (bug #4)**; unit-level only
- [ ] Redis / Postgres+Redis storage — **skip-noted**, no local infra
- [ ] AgentCore `/invocations` — **skip-noted**, needs AWS Bedrock
- [x] Streamable MCP smoke suite (`test_mcp_streamable.py`) passes live
- [ ] Legacy SSE smoke suite (`test_mcp_e2e.py`) — **wrong default URL (bug #6)**, self-skips

---

_Generated from a code-level review of `src/ad_buyer` (API surface, business flows, adversarial bug hunt) plus three live local runs against a real Anthropic API key, across which all 11 REST endpoints and all 40 MCP tools were individually exercised. Bug line numbers reference the state of the repo at review time (`main` @ commit `0823518`); re-verify after any rebase._
