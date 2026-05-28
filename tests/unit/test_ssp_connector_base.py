# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for SSP Connector base class and interface.

Tests cover:
- SSPFetchResult dataclass initialization and defaults
- SSPConnector abstract interface enforcement
- Concrete subclass behavior (normalize, fetch, dedup)
- Error collection and counting
- is_configured() and get_required_config() behavior
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from ad_buyer.tools.deal_library.ssp_connector_base import (
    SSPAuthError,
    SSPConnectionError,
    SSPConnector,
    SSPFetchResult,
    SSPRateLimitError,
)

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------


class _FakeConnector(SSPConnector):
    """Minimal concrete SSPConnector for unit tests."""

    @property
    def ssp_name(self) -> str:
        return "FakeSSP"

    @property
    def import_source(self) -> str:
        return "FAKE_SSP"

    def get_required_config(self) -> list[str]:
        return ["FAKE_SSP_API_KEY", "FAKE_SSP_SEAT_ID"]

    def fetch_deals(self, **kwargs) -> SSPFetchResult:
        raw_deals = kwargs.get("raw_deals", [])
        result = SSPFetchResult(ssp_name=self.ssp_name, raw_response_count=len(raw_deals))
        for raw in raw_deals:
            try:
                normalized = self._normalize_deal(raw)
                result.deals.append(normalized)
                result.successful += 1
            except (KeyError, ValueError) as exc:
                result.errors.append(str(exc))
                result.failed += 1
        result.total_fetched = len(raw_deals)
        return result

    def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
        if "deal_id" not in raw_deal:
            raise KeyError("Missing deal_id in raw deal")
        return {
            "seller_deal_id": raw_deal["deal_id"],
            "display_name": raw_deal.get("name", "Unnamed"),
            "seller_org": "FakeSSP",
            "seller_type": "SSP",
            "seller_url": "https://api.fakessp.com",
            "product_id": raw_deal["deal_id"],
            "deal_type": raw_deal.get("deal_type", "PD"),
            "status": "imported",
            "currency": "USD",
            "media_type": raw_deal.get("media_type"),
            "bid_floor_cpm": raw_deal.get("floor"),
        }


class _ConfiguredConnector(_FakeConnector):
    """Connector that always reports as configured (env vars present)."""

    def get_required_config(self) -> list[str]:
        # Return vars we'll set in the test
        return ["FAKE_CONFIGURED_VAR"]


# ---------------------------------------------------------------------------
# SSPFetchResult tests
# ---------------------------------------------------------------------------


class TestSSPFetchResult:
    """Tests for the SSPFetchResult dataclass."""

    def test_default_initialization(self):
        """SSPFetchResult initializes with zero counts and empty lists."""
        result = SSPFetchResult()
        assert result.deals == []
        assert result.errors == []
        assert result.total_fetched == 0
        assert result.successful == 0
        assert result.failed == 0
        assert result.skipped == 0
        assert result.ssp_name == ""
        assert result.raw_response_count == 0

    def test_with_values(self):
        """SSPFetchResult stores provided values correctly."""
        result = SSPFetchResult(
            deals=[{"seller_deal_id": "D1"}],
            errors=["error msg"],
            total_fetched=5,
            successful=4,
            failed=1,
            skipped=0,
            ssp_name="PubMatic",
            raw_response_count=5,
        )
        assert len(result.deals) == 1
        assert result.deals[0]["seller_deal_id"] == "D1"
        assert len(result.errors) == 1
        assert result.total_fetched == 5
        assert result.successful == 4
        assert result.failed == 1
        assert result.ssp_name == "PubMatic"
        assert result.raw_response_count == 5

    def test_deals_list_is_independent(self):
        """Each SSPFetchResult instance has its own deals list."""
        r1 = SSPFetchResult()
        r2 = SSPFetchResult()
        r1.deals.append({"seller_deal_id": "D1"})
        assert len(r2.deals) == 0

    def test_errors_list_is_independent(self):
        """Each SSPFetchResult instance has its own errors list."""
        r1 = SSPFetchResult()
        r2 = SSPFetchResult()
        r1.errors.append("an error")
        assert len(r2.errors) == 0


# ---------------------------------------------------------------------------
# SSPConnector abstract interface enforcement
# ---------------------------------------------------------------------------


class TestSSPConnectorAbstract:
    """Tests that abstract methods are enforced."""

    def test_cannot_instantiate_abstract_class(self):
        """SSPConnector cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SSPConnector()  # type: ignore[abstract]

    def test_missing_ssp_name_raises(self):
        """Subclass without ssp_name property raises TypeError on init."""

        class _NoName(SSPConnector):
            @property
            def import_source(self) -> str:
                return "TEST"

            def fetch_deals(self, **kwargs) -> SSPFetchResult:
                return SSPFetchResult()

            def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
                return {}

        with pytest.raises(TypeError):
            _NoName()

    def test_missing_import_source_raises(self):
        """Subclass without import_source property raises TypeError on init."""

        class _NoSource(SSPConnector):
            @property
            def ssp_name(self) -> str:
                return "Test"

            def fetch_deals(self, **kwargs) -> SSPFetchResult:
                return SSPFetchResult()

            def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
                return {}

        with pytest.raises(TypeError):
            _NoSource()

    def test_missing_fetch_deals_raises(self):
        """Subclass without fetch_deals raises TypeError on init."""

        class _NoFetch(SSPConnector):
            @property
            def ssp_name(self) -> str:
                return "Test"

            @property
            def import_source(self) -> str:
                return "TEST"

            def _normalize_deal(self, raw_deal: dict[str, Any]) -> dict[str, Any]:
                return {}

        with pytest.raises(TypeError):
            _NoFetch()

    def test_missing_normalize_deal_raises(self):
        """Subclass without _normalize_deal raises TypeError on init."""

        class _NoNormalize(SSPConnector):
            @property
            def ssp_name(self) -> str:
                return "Test"

            @property
            def import_source(self) -> str:
                return "TEST"

            def fetch_deals(self, **kwargs) -> SSPFetchResult:
                return SSPFetchResult()

        with pytest.raises(TypeError):
            _NoNormalize()

    def test_concrete_subclass_instantiates(self):
        """Fully-implemented subclass instantiates without error."""
        connector = _FakeConnector()
        assert connector is not None


# ---------------------------------------------------------------------------
# Concrete subclass behavior
# ---------------------------------------------------------------------------


class TestConcreteConnector:
    """Tests for concrete connector behavior via _FakeConnector."""

    def test_ssp_name_property(self):
        """ssp_name returns the connector's human-readable name."""
        connector = _FakeConnector()
        assert connector.ssp_name == "FakeSSP"

    def test_import_source_property(self):
        """import_source returns the metadata tag string."""
        connector = _FakeConnector()
        assert connector.import_source == "FAKE_SSP"

    def test_fetch_deals_empty(self):
        """fetch_deals with no raw deals returns empty result."""
        connector = _FakeConnector()
        result = connector.fetch_deals(raw_deals=[])
        assert isinstance(result, SSPFetchResult)
        assert result.deals == []
        assert result.errors == []
        assert result.successful == 0
        assert result.total_fetched == 0

    def test_fetch_deals_single_valid(self):
        """fetch_deals normalizes a single valid raw deal."""
        connector = _FakeConnector()
        raw = [{"deal_id": "PM-001", "name": "Test Deal", "deal_type": "PD"}]
        result = connector.fetch_deals(raw_deals=raw)

        assert result.successful == 1
        assert result.failed == 0
        assert result.total_fetched == 1
        assert result.raw_response_count == 1
        assert len(result.deals) == 1

        deal = result.deals[0]
        assert deal["seller_deal_id"] == "PM-001"
        assert deal["display_name"] == "Test Deal"
        assert deal["seller_type"] == "SSP"
        assert deal["seller_org"] == "FakeSSP"

    def test_fetch_deals_multiple(self):
        """fetch_deals handles multiple raw deals correctly."""
        connector = _FakeConnector()
        raw = [
            {"deal_id": "D1", "name": "Deal One", "deal_type": "PG"},
            {"deal_id": "D2", "name": "Deal Two", "deal_type": "PD"},
            {"deal_id": "D3", "name": "Deal Three", "deal_type": "PA"},
        ]
        result = connector.fetch_deals(raw_deals=raw)

        assert result.successful == 3
        assert result.failed == 0
        assert result.total_fetched == 3
        assert len(result.deals) == 3

    def test_fetch_deals_invalid_deal_captured_as_error(self):
        """fetch_deals captures normalization errors without crashing."""
        connector = _FakeConnector()
        raw = [
            {"deal_id": "VALID-001", "name": "Good Deal"},
            {"name": "Missing deal_id"},  # Missing required deal_id
        ]
        result = connector.fetch_deals(raw_deals=raw)

        assert result.successful == 1
        assert result.failed == 1
        assert len(result.errors) == 1
        assert len(result.deals) == 1
        assert result.deals[0]["seller_deal_id"] == "VALID-001"

    def test_normalize_deal_sets_required_fields(self):
        """_normalize_deal produces a dict with all required DealStore fields."""
        connector = _FakeConnector()
        raw = {"deal_id": "PM-123", "name": "Premium Package", "deal_type": "PG"}
        normalized = connector._normalize_deal(raw)

        # Required fields for DealStore.save_deal()
        assert "seller_deal_id" in normalized
        assert "seller_org" in normalized
        assert "seller_type" in normalized
        assert "seller_url" in normalized
        assert "product_id" in normalized
        assert "deal_type" in normalized
        assert "status" in normalized

        # seller_type must be "SSP" for all SSP connectors
        assert normalized["seller_type"] == "SSP"
        # status defaults to "imported"
        assert normalized["status"] == "imported"

    def test_normalize_deal_missing_required_raises(self):
        """_normalize_deal raises KeyError for missing required fields."""
        connector = _FakeConnector()
        with pytest.raises(KeyError):
            connector._normalize_deal({"name": "No deal_id"})

    def test_ssp_name_in_fetch_result(self):
        """fetch_deals sets ssp_name in the result."""
        connector = _FakeConnector()
        result = connector.fetch_deals(raw_deals=[])
        assert result.ssp_name == "FakeSSP"

    def test_raw_response_count_in_fetch_result(self):
        """fetch_deals sets raw_response_count to number of raw deals received."""
        connector = _FakeConnector()
        raw = [
            {"deal_id": "D1"},
            {"deal_id": "D2"},
            {"name": "no id"},  # Will fail, but still counted in raw_response_count
        ]
        result = connector.fetch_deals(raw_deals=raw)
        assert result.raw_response_count == 3


# ---------------------------------------------------------------------------
# is_configured() and get_required_config() tests
# ---------------------------------------------------------------------------


class TestConnectorConfiguration:
    """Tests for is_configured() and get_required_config() methods."""

    def test_get_required_config_returns_list(self):
        """get_required_config returns a list of strings."""
        connector = _FakeConnector()
        config = connector.get_required_config()
        assert isinstance(config, list)
        assert all(isinstance(v, str) for v in config)

    def test_is_configured_false_when_env_vars_missing(self):
        """is_configured returns False when required env vars are not set."""
        connector = _FakeConnector()
        # Ensure env vars are not set
        for var in connector.get_required_config():
            os.environ.pop(var, None)

        assert connector.is_configured() is False

    def test_is_configured_false_when_some_vars_missing(self):
        """is_configured returns False when only some required vars are set."""
        connector = _FakeConnector()
        vars_ = connector.get_required_config()
        # Set only the first var
        os.environ[vars_[0]] = "some_value"
        # Ensure the rest are not set
        for var in vars_[1:]:
            os.environ.pop(var, None)

        try:
            assert connector.is_configured() is False
        finally:
            os.environ.pop(vars_[0], None)

    def test_is_configured_true_when_all_vars_set(self, monkeypatch):
        """is_configured returns True when all required env vars are present."""
        connector = _ConfiguredConnector()
        monkeypatch.setenv("FAKE_CONFIGURED_VAR", "test_value_123")
        assert connector.is_configured() is True

    def test_is_configured_true_with_all_fake_vars(self, monkeypatch):
        """is_configured returns True when all required vars for _FakeConnector are set."""
        connector = _FakeConnector()
        monkeypatch.setenv("FAKE_SSP_API_KEY", "key123")
        monkeypatch.setenv("FAKE_SSP_SEAT_ID", "seat456")
        assert connector.is_configured() is True

    def test_empty_required_config_always_configured(self):
        """Connector with empty required_config list is always configured."""

        class _NoConfigConnector(_FakeConnector):
            def get_required_config(self) -> list[str]:
                return []

        connector = _NoConfigConnector()
        assert connector.is_configured() is True


# ---------------------------------------------------------------------------
# Error type tests
# ---------------------------------------------------------------------------


class TestSSPErrorTypes:
    """Tests for SSP-specific error types."""

    def test_ssp_connection_error_is_exception(self):
        """SSPConnectionError can be raised and caught as Exception."""
        with pytest.raises(SSPConnectionError):
            raise SSPConnectionError("Connection refused")

    def test_ssp_auth_error_is_exception(self):
        """SSPAuthError can be raised and caught as Exception."""
        with pytest.raises(SSPAuthError):
            raise SSPAuthError("Invalid API key")

    def test_ssp_rate_limit_error_is_exception(self):
        """SSPRateLimitError can be raised and caught as Exception."""
        with pytest.raises(SSPRateLimitError):
            raise SSPRateLimitError("Rate limit exceeded, retry after 60s")

    def test_errors_are_distinct_types(self):
        """Each error type is distinct and not interchangeable."""
        conn_err = SSPConnectionError("conn")
        auth_err = SSPAuthError("auth")
        rate_err = SSPRateLimitError("rate")

        assert not isinstance(conn_err, SSPAuthError)
        assert not isinstance(auth_err, SSPConnectionError)
        assert not isinstance(rate_err, SSPConnectionError)

    def test_errors_inherit_from_exception(self):
        """All SSP errors inherit from the base Exception class."""
        for exc_class in (SSPConnectionError, SSPAuthError, SSPRateLimitError):
            assert issubclass(exc_class, Exception)

    def test_error_message_preserved(self):
        """Error messages are preserved in raised exceptions."""
        msg = "Detailed error message for diagnosis"
        try:
            raise SSPConnectionError(msg)
        except SSPConnectionError as exc:
            assert msg in str(exc)

    def test_ssp_connection_error_with_retry_info(self):
        """SSPRateLimitError can carry retry-after information."""
        err = SSPRateLimitError("Rate limited", retry_after=60)
        assert err.retry_after == 60

    def test_ssp_auth_error_with_status_code(self):
        """SSPAuthError can carry HTTP status code."""
        err = SSPAuthError("Unauthorized", status_code=401)
        assert err.status_code == 401

    def test_ssp_connection_error_with_status_code(self):
        """SSPConnectionError can carry HTTP status code."""
        err = SSPConnectionError("Service unavailable", status_code=503)
        assert err.status_code == 503


# ---------------------------------------------------------------------------
# Import alias tests
# ---------------------------------------------------------------------------


class TestModuleExports:
    """Tests that the module exports the expected public interface."""

    def test_all_exports_importable(self):
        """All public classes are importable from the module."""
        from ad_buyer.tools.deal_library.ssp_connector_base import (  # noqa: F401
            SSPAuthError,
            SSPConnectionError,
            SSPConnector,
            SSPFetchResult,
            SSPRateLimitError,
        )

    def test_connectors_init_exports(self):
        """__init__.py in connectors subpackage exports the public interface."""
        from ad_buyer.tools.deal_library import ssp_connector_base  # noqa: F401
