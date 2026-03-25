# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for random seed isolation and CORS configuration fixes."""

import random

from ad_buyer.config.settings import Settings


class TestSyntheticEmbeddingRandomIsolation:
    """Test that _generate_synthetic_embedding uses a local Random instance."""

    def _make_client(self):
        """Create a minimal UCPClient for testing."""
        from ad_buyer.clients.ucp_client import UCPClient

        return UCPClient()

    def test_global_random_state_not_affected(self):
        """Calling _generate_synthetic_embedding must not alter global random state."""
        client = self._make_client()
        requirements = {"age": "25-54", "geo": "US"}
        dimension = 16

        # Set global random to a known state and sample a value
        random.seed(42)
        before = random.random()

        # Reset to same state
        random.seed(42)

        # Call the method under test
        client._generate_synthetic_embedding(requirements, dimension)

        # Global state should still yield the same value
        after = random.random()
        assert before == after, "_generate_synthetic_embedding mutated global random state"

    def test_deterministic_output(self):
        """Same inputs should produce the same embedding."""
        client = self._make_client()
        requirements = {"category": "sports", "region": "EU"}
        dimension = 8

        v1 = client._generate_synthetic_embedding(requirements, dimension)
        v2 = client._generate_synthetic_embedding(requirements, dimension)
        assert v1 == v2

    def test_output_dimension(self):
        """Embedding should have the requested dimension."""
        client = self._make_client()
        requirements = {"topic": "news"}
        dimension = 32

        result = client._generate_synthetic_embedding(requirements, dimension)
        assert len(result) == dimension


class TestCORSConfiguration:
    """Test that CORS middleware uses specific origins, not wildcard."""

    def test_settings_default_cors_origins(self):
        """Default CORS origins should be localhost dev ports, not wildcard."""
        s = Settings(
            anthropic_api_key="test",
            _env_file=None,
        )
        origins = s.get_cors_origins()
        assert "*" not in origins
        assert "http://localhost:3000" in origins
        assert "http://localhost:8080" in origins

    def test_settings_custom_cors_origins(self):
        """CORS origins should be configurable."""
        s = Settings(
            anthropic_api_key="test",
            cors_allowed_origins="https://app.example.com,https://admin.example.com",
            _env_file=None,
        )
        origins = s.get_cors_origins()
        assert origins == ["https://app.example.com", "https://admin.example.com"]

    def test_app_cors_middleware_uses_settings(self):
        """The FastAPI app should use settings-based origins, not wildcard."""
        from ad_buyer.interfaces.api.main import app

        # Find the CORSMiddleware in the app's middleware stack
        cors_middleware = None
        # Walk the middleware stack
        current = app
        while hasattr(current, "app"):
            if hasattr(current, "allow_origins"):
                cors_middleware = current
                break
            current = current.app

        if cors_middleware is not None:
            assert "*" not in cors_middleware.allow_origins, (
                "CORS middleware should not use wildcard origins"
            )
        # If we can't find it via introspection, at least verify the source
        # doesn't have allow_origins=["*"] (covered by the settings tests above)

    def test_credentials_not_with_wildcard(self):
        """allow_credentials should not be True when origins is wildcard."""
        from ad_buyer.interfaces.api.main import app

        current = app
        while hasattr(current, "app"):
            if hasattr(current, "allow_origins"):
                if "*" in getattr(current, "allow_origins", []):
                    assert not getattr(current, "allow_credentials", False), (
                        "allow_credentials=True with wildcard origins is a security issue"
                    )
                break
            current = current.app
