# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests verifying no hardcoded server URLs remain in client code."""

import ast
import os

import pytest

# Source files that must not contain hardcoded Cloud Run URLs
CLIENT_SOURCE_FILES = [
    "src/ad_buyer/clients/unified_client.py",
    "src/ad_buyer/clients/mcp_client.py",
    "src/ad_buyer/clients/a2a_client.py",
    "src/ad_buyer/flows/dsp_deal_flow.py",
]

# The URL pattern that should not appear as a default parameter value
HARDCODED_URL = "agentic-direct-server"


def _repo_root() -> str:
    """Return the ad_buyer_system repo root."""
    # Walk up from this test file to find the repo root
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


class TestNoHardcodedURLs:
    """Verify that Cloud Run URLs are not hardcoded as default parameters."""

    @pytest.mark.parametrize("rel_path", CLIENT_SOURCE_FILES)
    def test_no_hardcoded_url_in_source(self, rel_path: str) -> None:
        """Source files must not contain the Cloud Run URL as a default parameter."""
        filepath = os.path.join(_repo_root(), rel_path)
        with open(filepath) as f:
            source = f.read()

        tree = ast.parse(source)

        for node in ast.walk(tree):
            # Check function/method default argument values
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default is None:
                        continue
                    if isinstance(default, ast.Constant) and isinstance(default.value, str):
                        assert HARDCODED_URL not in default.value, (
                            f"{rel_path}: function '{node.name}' has hardcoded "
                            f"server URL as default parameter"
                        )


class TestSettingsHasIABServerURL:
    """Verify Settings class exposes iab_server_url field."""

    def test_settings_field_exists(self) -> None:
        """Settings must have an iab_server_url field."""
        from ad_buyer.config.settings import Settings

        # iab_server_url should be a required field (no default)
        fields = Settings.model_fields
        assert "iab_server_url" in fields, "Settings missing iab_server_url field"

    def test_settings_has_local_default(self) -> None:
        """Settings iab_server_url should default to localhost (not a remote URL)."""
        from ad_buyer.config.settings import Settings

        field_info = Settings.model_fields["iab_server_url"]
        assert field_info.default == "http://localhost:8001", (
            "iab_server_url should default to localhost:8001"
        )

    def test_settings_loads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Settings should load iab_server_url from IAB_SERVER_URL env var."""
        from ad_buyer.config.settings import Settings

        monkeypatch.setenv("IAB_SERVER_URL", "https://test-server.example.com")
        # Create a fresh instance (bypass lru_cache)
        s = Settings()
        assert s.iab_server_url == "https://test-server.example.com"
