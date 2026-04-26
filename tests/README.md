# Buyer Agent Test Suite

This directory holds the buyer agent's test suite. As of `feature/stability-sweep`,
the suite has **~3076 tests** organized into three tiers.

## Layout

| Directory | Count | Purpose | Run cost |
|-----------|------:|---------|----------|
| `tests/unit/` | ~100 files | Pure-Python unit tests; no network, no LLM, no real seller. Fast. | ~5–10 s per file, ~3 min total |
| `tests/integration/` | ~15 files | Cross-module / cross-flow tests, mocked seller endpoints. May spin up `httpx.Mock` or in-process FastAPI. | ~30 s per file, ~5 min total |
| `tests/smoke/` | 1 file | Live-server smoke (currently `test_mcp_e2e.py`). Marked `@pytest.mark.smoke`; only run on demand. | seconds per test, but requires a running MCP server |

## Running

### Quick — unit only

```bash
PYTHONPATH=src venv/bin/pytest tests/unit/ -q
```

### Full — unit + integration (the canonical CI run)

```bash
PYTHONPATH=src venv/bin/pytest tests/ --tb=short -q
```

Expected: **3076 passed, 41 skipped, 0 failed** (occasionally 1 known flake — see [Flakes](#flakes)).

### Smoke — requires live MCP server

```bash
PYTHONPATH=src venv/bin/pytest tests/smoke/ -m smoke -v
```

### Single test (any tier)

```bash
PYTHONPATH=src venv/bin/pytest tests/unit/test_audience_planner_wiring.py::TestEmbeddingMintTool -v
```

## Conventions

- **`PYTHONPATH=src`** is required because the buyer ships its package as `src/ad_buyer/`. Running pytest without it triggers `ModuleNotFoundError`.
- **Worktrees**: when running in a `.worktrees/<name>/` checkout, `ln -sf ../../venv venv` lets the worktree share the main repo's venv.
- **Audience-extension cross-repo tests** (e.g. `test_path_a_audience_e2e.py::test_cross_repo_audience_plan_json_round_trip`) need the seller worktree's `src/` on the path. The test discovers it from the buyer worktree name, but you can override with `AD_SELLER_SRC_PATH=/abs/path/to/ad_seller_system/src`.
- **CrewAI agents need `ANTHROPIC_API_KEY`**. Tests `os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")` at module top to keep CI green without a real key.
- **`EMBEDDING_MODE` env var** controls embedding behavior. CI defaults to `mock`; `hybrid` is the runtime default.

## Audit / regression guards

A few tests exist purely to lock in invariants and prevent future drift:

| Guard | Purpose |
|-------|---------|
| `test_endpoint_no_flow_kickoff.py` | Seller read endpoints (`/products`, `/.well-known/agent.json`, `/api/v1/quotes`) must not call `Flow.kickoff()` per request. Autouse fixture monkeypatches `kickoff` to raise (ar-uwad / ar-0vtg). |
| `test_tool_return_type_hints.py` | Every `BaseTool` subclass's `_run` / `_arun` method must declare a return type. Parametrized walk over `ad_buyer.tools.*` (ar-gsd). |
| `test_schema_drift_canonical.py` | `AudienceRef` / `AudiencePlan` JSON Schema must match the canonical snapshot at `agent_range/docs/api/audience_plan_schemas.json` (E2-10). The seller has a mirror test for cross-repo drift detection. |
| `test_settings_lazy_init.py` | Importing `ad_buyer.config.settings` must not eagerly construct `Settings()`. Tests assert env-var overrides win when applied before first attribute access (ar-le3). |

If any of these fail, you've introduced drift — read the failing assertion, then fix the underlying code (don't update the guard).

## Flakes

- **`test_threshold_recalibration.py::TestThresholds::test_lookup_per_mode`** (`ar-0isf`): order-dependent — passes in isolation, fails when run after other tests that mutate `settings.embedding_mode` without restoring. Tracked separately; not introduced by any specific bead.

If you discover a new flake:
1. Confirm it passes in isolation: `pytest <path>::<test> -v`
2. Confirm it's order-dependent: re-run the full suite a couple of times
3. File a new bead and add it here

## Audience-extension tests by epic

| Bead / scope | File |
|---|---|
| Epic 1 § 3 — typed AudienceRef + AudiencePlan | `test_audience_plan.py`, `test_taxonomy_loader.py`, `test_taxonomy_lookup_tool.py` |
| Epic 1 § 4 — brief migration + strictness + content-taxonomy validation | `test_campaign_brief_migration.py`, `test_audience_strictness.py`, `test_brief_ingestion_validation.py` |
| Epic 1 § 5 — orchestrator audience_plan field | `test_orchestrator_audience_plan.py` |
| Epic 1 § 6 — Audience Planner wiring | `test_audience_planner_wiring.py` |
| Epic 1 § 7 — reasoning loop | `test_audience_planner_reasoning.py` |
| Epic 1 § 12 — degradation + retry | `test_audience_degradation.py`, `test_seller_retry_on_rejection.py` |
| Epic 1 § 13 — pre-flight integration | `test_buyer_preflight.py` |
| Epic 1 § 13a — audit log | `test_audience_audit_log.py` |
| Epic 1 § 14b — dual content-type | `test_deals_client_dual_content_type.py` |
| Epic 1 § 15 — OpenRTB carrier mapping | `test_openrtb_builder.py` |
| Epic 1 § 16 — E2E Path A + cross-repo round-trip | `tests/integration/test_path_a_audience_e2e.py` |
| Epic 1 § 18/19/20 — Path B (DSPDealFlow + channel crews) | `test_buyer_deal_flow_audience.py`, `test_channel_crew_audience_invocation.py`, `tests/integration/test_path_b_audience_e2e.py` |
| Epic 2 — real model + drift hardening | `test_real_embedding_model.py`, `test_embedding_eval.py`, `test_threshold_recalibration.py`, `tests/integration/test_real_model_path_e2e.py`, `tests/integration/test_schema_drift_canonical.py` |
| Stability sweep | `test_endpoint_no_flow_kickoff.py`, `test_tool_to_natural_language.py`, `test_tool_return_type_hints.py`, `test_settings_lazy_init.py`, `test_reject_global_agentic.py` |

## Adding a new test

1. Pick the right tier (unit unless you genuinely need cross-module setup).
2. Mirror existing patterns — copy a small file as a template.
3. Set `os.environ.setdefault("ANTHROPIC_API_KEY", ...)` if you import any agent code.
4. If you're testing a tool, lean on the `BaseTool` regression guards above.
5. Run `pytest tests/<your-new-file>.py -v` then the full suite before committing.
