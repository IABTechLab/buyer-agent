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
