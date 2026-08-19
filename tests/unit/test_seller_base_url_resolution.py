# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seller base-URL resolution for single-seller OpenDirect-style calls.

Community-reported (issue #114 follow-up, 2026-07-31): run-demo.sh exports
``SELLER_ENDPOINTS=http://localhost:8000`` but ``POST /products/search``
failed with "All connection attempts failed" because the API route's
``_create_client()`` read only ``settings.opendirect_base_url``, whose
default must point at the seller agent (``http://localhost:8000/api/v2.1``).

Contract under test:

1. ``Settings.resolve_seller_base_url()`` prefers the first
   ``SELLER_ENDPOINTS`` entry (the modern seller wiring, and what
   run-demo.sh exports) and falls back to the legacy
   ``OPENDIRECT_BASE_URL`` when no seller endpoints are configured.
   This mirrors the chat interface's existing precedence.
2. The API and CLI ``_create_client()`` factories build their
   ``OpenDirectClient`` from that resolution, so the demo wiring works
   with SELLER_ENDPOINTS alone.
"""

import importlib

settings_module = importlib.import_module("ad_buyer.config.settings")
Settings = settings_module.Settings


class TestResolveSellerBaseUrl:
    def test_prefers_first_seller_endpoint(self, monkeypatch):
        monkeypatch.setenv("SELLER_ENDPOINTS", "http://localhost:8000")
        monkeypatch.delenv("OPENDIRECT_BASE_URL", raising=False)
        assert Settings().resolve_seller_base_url() == "http://localhost:8000"

    def test_first_of_multiple_endpoints_wins(self, monkeypatch):
        monkeypatch.setenv(
            "SELLER_ENDPOINTS",
            "http://seller-a:8000, http://seller-b:8002",
        )
        assert Settings().resolve_seller_base_url() == "http://seller-a:8000"

    def test_opendirect_field_default_is_seller_port(self):
        """Documented default points at the seller agent on :8000."""
        assert (
            Settings.model_fields["opendirect_base_url"].default == "http://localhost:8000/api/v2.1"
        )

    def test_falls_back_to_legacy_opendirect_default(self, monkeypatch):
        monkeypatch.delenv("SELLER_ENDPOINTS", raising=False)
        # Pin the documented default so a developer .env cannot shadow it.
        monkeypatch.setenv("OPENDIRECT_BASE_URL", "http://localhost:8000/api/v2.1")
        assert Settings().resolve_seller_base_url() == "http://localhost:8000/api/v2.1"

    def test_falls_back_to_explicit_opendirect_url(self, monkeypatch):
        monkeypatch.delenv("SELLER_ENDPOINTS", raising=False)
        monkeypatch.setenv("OPENDIRECT_BASE_URL", "http://opendirect.example:9000/api/v2.1")
        assert Settings().resolve_seller_base_url() == "http://opendirect.example:9000/api/v2.1"

    def test_seller_endpoints_beat_explicit_opendirect_url(self, monkeypatch):
        """SELLER_ENDPOINTS is the primary wiring; legacy URL only fills gaps."""
        monkeypatch.setenv("SELLER_ENDPOINTS", "http://localhost:8000")
        monkeypatch.setenv("OPENDIRECT_BASE_URL", "http://opendirect.example:9000/api/v2.1")
        assert Settings().resolve_seller_base_url() == "http://localhost:8000"

    def test_blank_seller_endpoints_are_ignored(self, monkeypatch):
        monkeypatch.setenv("SELLER_ENDPOINTS", " , ")
        monkeypatch.setenv("OPENDIRECT_BASE_URL", "http://localhost:8000/api/v2.1")
        assert Settings().resolve_seller_base_url() == "http://localhost:8000/api/v2.1"


class TestCreateClientUsesResolution:
    """The run-demo wiring: SELLER_ENDPOINTS alone must reach the seller."""

    def _fresh_settings(self, monkeypatch, **env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return Settings()

    def test_api_create_client_uses_seller_endpoint(self, monkeypatch):
        api_main = importlib.import_module("ad_buyer.interfaces.api.main")
        monkeypatch.setattr(
            api_main,
            "settings",
            self._fresh_settings(monkeypatch, SELLER_ENDPOINTS="http://localhost:8000"),
        )
        client = api_main._create_client()
        assert client.base_url == "http://localhost:8000"

    def test_api_create_client_legacy_fallback(self, monkeypatch):
        api_main = importlib.import_module("ad_buyer.interfaces.api.main")
        monkeypatch.delenv("SELLER_ENDPOINTS", raising=False)
        monkeypatch.setattr(
            api_main,
            "settings",
            self._fresh_settings(monkeypatch, OPENDIRECT_BASE_URL="http://localhost:8000/api/v2.1"),
        )
        client = api_main._create_client()
        assert client.base_url == "http://localhost:8000/api/v2.1"

    def test_cli_create_client_uses_seller_endpoint(self, monkeypatch):
        cli_main = importlib.import_module("ad_buyer.interfaces.cli.main")
        monkeypatch.setattr(
            cli_main,
            "settings",
            self._fresh_settings(monkeypatch, SELLER_ENDPOINTS="http://localhost:8000"),
        )
        client = cli_main._create_client()
        assert client.base_url == "http://localhost:8000"
