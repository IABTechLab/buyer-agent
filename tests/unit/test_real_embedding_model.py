"""E2-2: real embedding model — basic mode tests.

Per docs/decisions/EMBEDDING_STRATEGY_2026-04-25.md (sentence-transformers
all-MiniLM-L6-v2 local + advertiser-supplied + mock fallback hybrid).
"""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

import pytest
from unittest.mock import patch

from ad_buyer.config.settings import settings
from ad_buyer.clients.ucp_client import UCPClient

try:
    import sentence_transformers  # noqa: F401

    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False


REQS = {"interest": "auto", "age": "25-54"}


class TestEmbeddingModes:
    def test_settings_field_exists(self):
        assert hasattr(settings, "embedding_mode")
        assert settings.embedding_mode in ("mock", "local", "advertiser", "hybrid")

    def test_mock_mode_deterministic(self):
        with patch.object(settings, "embedding_mode", "mock"):
            client = UCPClient()
            r1 = client.create_query_embedding_with_provenance(REQS)
            r2 = client.create_query_embedding_with_provenance(REQS)
        assert r1.provenance == "mock"
        assert r2.provenance == "mock"
        assert r1.embedding.vector == r2.embedding.vector
        assert r1.dimension == r2.dimension

    def test_advertiser_mode_uses_supplied_vector(self):
        sample = [0.1] * 384
        with patch.object(settings, "embedding_mode", "advertiser"):
            client = UCPClient()
            r = client.create_query_embedding_with_provenance(
                REQS, advertiser_vector=sample
            )
        assert r.provenance == "advertiser_supplied"
        assert r.embedding.vector == sample
        assert r.dimension == 384

    def test_advertiser_dim_out_of_range_falls_back(self):
        # 100-dim is below the 256 floor → fall back to mock (or local if hybrid)
        bad = [0.5] * 100
        with patch.object(settings, "embedding_mode", "advertiser"):
            client = UCPClient()
            r = client.create_query_embedding_with_provenance(
                REQS, advertiser_vector=bad
            )
        # Out-of-range advertiser vector skipped, mock used (mode=advertiser
        # has no local fallback configured, so mock is the safe default).
        assert r.provenance == "mock"
        assert r.embedding.vector != bad

    def test_hybrid_mode_advertiser_wins(self):
        sample = [0.2] * 384
        with patch.object(settings, "embedding_mode", "hybrid"):
            client = UCPClient()
            r = client.create_query_embedding_with_provenance(
                REQS, advertiser_vector=sample
            )
        assert r.provenance == "advertiser_supplied"
        assert r.embedding.vector == sample

    def test_hybrid_mode_no_advertiser_falls_to_local_or_mock(self):
        with patch.object(settings, "embedding_mode", "hybrid"):
            client = UCPClient()
            r = client.create_query_embedding_with_provenance(REQS)
        # Either local (if SBERT loaded) or mock — both are acceptable
        assert r.provenance in ("local_buyer", "mock")
        assert 256 <= r.dimension <= 1024 or r.dimension == 384

    @pytest.mark.skipif(
        not SBERT_AVAILABLE, reason="sentence-transformers not installed"
    )
    def test_local_mode_loads_real_model(self):
        # Best-effort: model download may be blocked in CI. Either way the
        # function returns a well-formed result.
        with patch.object(settings, "embedding_mode", "local"):
            client = UCPClient()
            r = client.create_query_embedding_with_provenance(REQS)
        # Either local model loaded → 384-dim local_buyer, or fallback → mock
        assert r.provenance in ("local_buyer", "mock")
        if r.provenance == "local_buyer":
            assert r.dimension == 384

    def test_backward_compat_create_query_embedding(self):
        # Legacy callers without advertiser_vector should still get an
        # UCPEmbedding back from the original API.
        with patch.object(settings, "embedding_mode", "mock"):
            client = UCPClient()
            emb = client.create_query_embedding(REQS)
        # Old-API contract: returns a UCPEmbedding directly.
        assert hasattr(emb, "vector")
        assert len(emb.vector) > 0


class TestComplianceContextProvenance:
    def test_compliance_context_has_embedding_provenance(self):
        from ad_buyer.models.audience_plan import ComplianceContext

        ctx = ComplianceContext(
            jurisdiction="US",
            consent_framework="none",
            consent_string_ref=None,
            attestation=None,
        )
        assert hasattr(ctx, "embedding_provenance")
        assert ctx.embedding_provenance is None

    def test_compliance_context_accepts_provenance_values(self):
        from ad_buyer.models.audience_plan import ComplianceContext

        for provenance in (
            "mock",
            "local_buyer",
            "advertiser_supplied",
            "hosted_external",
        ):
            ctx = ComplianceContext(
                jurisdiction="US",
                consent_framework="IAB-TCFv2",
                embedding_provenance=provenance,
            )
            assert ctx.embedding_provenance == provenance
