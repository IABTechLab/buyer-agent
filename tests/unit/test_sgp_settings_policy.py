# Author: SafeGuard Privacy
# Donated to IAB Tech Lab

"""Tests for SGP_UNKNOWN_VENDOR_POLICY normalization at settings load.

Kept out of ``test_settings_lazy_init.py`` deliberately: that module reloads
``ad_buyer.config.settings``, which pollutes any test later in the same session
that captured the previous ``settings`` object. These tests only construct
``Settings`` directly, so they stay order-independent.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from ad_buyer.config.settings import Settings


class TestSgpUnknownVendorPolicy:
    def test_default_is_block(self) -> None:
        assert Settings().sgp_unknown_vendor_policy == "block"

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("block", "block"),
            ("BLOCK", "block"),
            ("Warn", "warn"),
            ("  ALLOW  ", "allow"),
        ],
    )
    def test_canonicalizes_case_and_whitespace(self, raw: str, expected: str) -> None:
        assert Settings(sgp_unknown_vendor_policy=raw).sgp_unknown_vendor_policy == expected

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_falls_back_to_the_safe_default(self, raw: str) -> None:
        """``SGP_UNKNOWN_VENDOR_POLICY=`` reads as unset, not as a startup error.

        Every other SGP variable treats empty as unconfigured, so an empty
        policy must not be the one thing that refuses to boot.
        """
        assert Settings(sgp_unknown_vendor_policy=raw).sgp_unknown_vendor_policy == "block"

    def test_unrecognized_value_fails_at_load(self) -> None:
        with pytest.raises(ValidationError, match="sgp_unknown_policy"):
            Settings(sgp_unknown_vendor_policy="maybe")

    def test_importing_settings_does_not_import_the_clients_package(self) -> None:
        """Settings must not depend on ``ad_buyer.clients``.

        The policy helper lives in ``models.sgp`` precisely to keep this edge
        one-directional: ``clients.ucp_client`` reads settings, so a
        settings -> clients import would be a latent circular import.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                # `from`-import: this module swaps itself for a lazy proxy in
                # sys.modules, so attribute access on the module object would
                # route through the proxy instead of finding the class.
                "import sys\n"
                "from ad_buyer.config.settings import Settings\n"
                "Settings()\n"
                "print('ad_buyer.clients' in sys.modules)\n",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False", result.stdout


class TestSgpRetrySettings:
    """Retry/timeout knobs are environment variables, not code edits."""

    def test_defaults(self) -> None:
        s = Settings()
        assert s.sgp_timeout_seconds == 15.0
        assert s.sgp_max_retries == 2
        assert s.sgp_retry_backoff_seconds == 0.5

    def test_values_are_read_from_the_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("SGP_TIMEOUT_SECONDS", "4.5")
        monkeypatch.setenv("SGP_MAX_RETRIES", "5")
        monkeypatch.setenv("SGP_RETRY_BACKOFF_SECONDS", "0")
        s = Settings()
        assert s.sgp_timeout_seconds == 4.5
        assert s.sgp_max_retries == 5
        assert s.sgp_retry_backoff_seconds == 0.0

    def test_retries_can_be_disabled_via_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SGP_MAX_RETRIES", "0")
        assert Settings().sgp_max_retries == 0

    @pytest.mark.parametrize(
        "var, value",
        [
            ("SGP_MAX_RETRIES", "-1"),
            ("SGP_RETRY_BACKOFF_SECONDS", "-0.5"),
            ("SGP_TIMEOUT_SECONDS", "0"),
        ],
    )
    def test_nonsensical_values_are_rejected(self, monkeypatch, var, value) -> None:
        """A zero timeout or negative retry budget is a config error, not a clamp."""
        monkeypatch.setenv(var, value)
        with pytest.raises(ValidationError):
            Settings()

    def test_settings_reach_the_constructed_client(self, monkeypatch) -> None:
        """The whole point: editing .env must change client behavior.

        Guards the wiring in ``deal_booking_flow._build_sgp_client`` -- settings
        that exist but are never passed through would look configured and do
        nothing.
        """
        from ad_buyer.flows.deal_booking_flow import _build_sgp_client

        monkeypatch.setenv("SGP_ENFORCE", "true")
        monkeypatch.setenv("SGP_API_KEY", "test-key")
        monkeypatch.setenv("SGP_TIMEOUT_SECONDS", "3")
        monkeypatch.setenv("SGP_MAX_RETRIES", "7")
        monkeypatch.setenv("SGP_RETRY_BACKOFF_SECONDS", "0.25")
        monkeypatch.setenv("SGP_CACHE_TTL_SECONDS", "60")

        client = _build_sgp_client(Settings())

        assert client is not None
        assert client._timeout == 3.0
        assert client._max_retries == 7
        assert client._retry_backoff == 0.25
        assert client._cache_ttl == 60
