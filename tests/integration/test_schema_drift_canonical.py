"""E2-10: cross-repo schema-drift hardening.

Asserts that the buyer's `AudienceRef`/`AudiencePlan`/`ComplianceContext`
Pydantic-emitted JSON Schemas match the canonical vendored snapshot at
`agent_range/docs/api/audience_plan_schemas.json`. The seller-side
counterpart of this test (in ad_seller_system/tests/integration/) does
the same check on the seller's mirror models.

If either side drifts from the snapshot, this test fails and CI flags
the divergence before the cross-repo round-trip silently breaks. To
update the snapshot intentionally:

  PYTHONPATH=src venv/bin/python -c \\
    'import json; from ad_buyer.models.audience_plan import AudienceRef, AudiencePlan, ComplianceContext; \\
     print(json.dumps({"ComplianceContext": ComplianceContext.model_json_schema(), \\
                       "AudienceRef": AudienceRef.model_json_schema(), \\
                       "AudiencePlan": AudiencePlan.model_json_schema()}, indent=2, sort_keys=True))' \\
    > /Users/aidancardella/dev/agent_range/.worktrees/audience-extension/docs/api/audience_plan_schemas.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest

from ad_buyer.models.audience_plan import (
    AudiencePlan,
    AudienceRef,
    ComplianceContext,
)


CANONICAL_SCHEMA_PATH = Path(
    "/Users/aidancardella/dev/agent_range/.worktrees/audience-extension/docs/api/audience_plan_schemas.json"
)


def _load_canonical() -> dict:
    if not CANONICAL_SCHEMA_PATH.exists():
        pytest.skip(f"Canonical schema snapshot not present: {CANONICAL_SCHEMA_PATH}")
    return json.loads(CANONICAL_SCHEMA_PATH.read_text())


def _live(model: type) -> dict:
    """Round-trip live schema through json to normalize ordering."""

    return json.loads(json.dumps(model.model_json_schema(), sort_keys=True))


def _canon(snapshot: dict) -> dict:
    return json.loads(json.dumps(snapshot, sort_keys=True))


class TestBuyerSchemaMatchesCanonical:
    def test_compliance_context(self):
        canonical = _load_canonical()["ComplianceContext"]
        live = _live(ComplianceContext)
        assert live == _canon(canonical)

    def test_audience_ref(self):
        canonical = _load_canonical()["AudienceRef"]
        live = _live(AudienceRef)
        assert live == _canon(canonical)

    def test_audience_plan(self):
        canonical = _load_canonical()["AudiencePlan"]
        live = _live(AudiencePlan)
        assert live == _canon(canonical)
