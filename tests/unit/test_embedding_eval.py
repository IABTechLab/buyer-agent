"""E2-3: embedding evaluation harness tests."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_buyer.eval import (
    EMBEDDING_EVAL_FIXTURES,
    evaluate_embedding_modes,
)


class TestEmbeddingEval:
    def test_default_run_returns_all_modes(self):
        report = evaluate_embedding_modes()
        modes_evaluated = [m.mode for m in report.per_mode]
        # Default modes covers all 4
        assert sorted(modes_evaluated) == ["advertiser", "hybrid", "local", "mock"]
        assert len(report.fixtures) == len(EMBEDDING_EVAL_FIXTURES)

    def test_mock_mode_is_deterministic(self):
        report = evaluate_embedding_modes(modes=["mock"])
        m = report.per_mode[0]
        assert m.deterministic is True
        # Mock embeddings have consistent dim across fixtures
        assert m.dimension > 0
        # Distinctiveness is in [0, 2] range
        assert 0.0 <= m.distinctiveness <= 2.0

    def test_per_mode_metrics_serialize(self):
        report = evaluate_embedding_modes(modes=["mock"])
        d = report.as_dict()
        assert "fixtures" in d
        assert "per_mode" in d
        assert len(d["per_mode"]) == 1
        assert d["per_mode"][0]["mode"] == "mock"
        assert "distinctiveness" in d["per_mode"][0]
        assert "deterministic" in d["per_mode"][0]
        assert "provenance" in d["per_mode"][0]

    def test_advertiser_mode_falls_back_without_supplied_vector(self):
        # advertiser mode without an advertiser_vector kwarg falls back to mock.
        # The harness doesn't pass advertiser_vector, so we expect mock provenance.
        report = evaluate_embedding_modes(modes=["advertiser"])
        m = report.per_mode[0]
        # Provenance reported is whatever the client actually used (mock fallback)
        assert "mock" in m.provenance
