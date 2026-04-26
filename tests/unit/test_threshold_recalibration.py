"""E2-4: per-mode similarity threshold tests."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from unittest.mock import patch

from ad_buyer.config.settings import settings
from ad_buyer.clients.ucp_client import (
    _SIMILARITY_THRESHOLDS,
    _similarity_thresholds_for_mode,
)


class TestThresholds:
    def test_all_modes_have_thresholds(self):
        for mode in ("mock", "local", "advertiser", "hybrid"):
            assert mode in _SIMILARITY_THRESHOLDS
            t = _SIMILARITY_THRESHOLDS[mode]
            for key in ("strong", "moderate", "weak"):
                assert key in t
                assert 0.0 <= t[key] <= 1.0

    def test_thresholds_are_monotonic(self):
        for mode, t in _SIMILARITY_THRESHOLDS.items():
            assert t["strong"] >= t["moderate"] >= t["weak"], mode

    def test_mock_is_tighter_than_local(self):
        # Mock SHA256 vectors saturate quickly → tighter strong threshold.
        assert _SIMILARITY_THRESHOLDS["mock"]["strong"] >= _SIMILARITY_THRESHOLDS["local"]["strong"]

    def test_lookup_per_mode(self):
        for mode in ("mock", "local", "advertiser", "hybrid"):
            with patch.object(settings, "embedding_mode", mode):
                t = _similarity_thresholds_for_mode()
                assert t == _SIMILARITY_THRESHOLDS[mode]
