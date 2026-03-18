# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for CSV deal import parser.

Tests cover column detection, data normalization, validation,
error handling, and import result reporting.
"""

import csv
import io
import os
import tempfile
from pathlib import Path

import pytest

from ad_buyer.tools.deal_import import (
    COLUMN_MAPPINGS,
    ImportError as DealImportError,
    ImportResult,
    parse_csv_deals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(rows: list[list[str]], *, tmpdir: str | None = None) -> str:
    """Write rows to a temp CSV file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv", dir=tmpdir)
    with os.fdopen(fd, "w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return path


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


class TestBasicParsing:
    """Test basic CSV parsing with standard column names."""

    def test_single_valid_row(self, tmp_path):
        """Parse a single valid deal row with standard column names."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm", "deal_type", "media_type"],
                ["DEAL-001", "Test Deal", "ESPN", "12.50", "PG", "DIGITAL"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)

        assert result.total_rows == 1
        assert result.successful == 1
        assert result.failed == 0
        assert result.skipped == 0
        assert len(result.deals) == 1

        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        assert deal["display_name"] == "Test Deal"
        assert deal["seller_org"] == "ESPN"
        assert deal["fixed_price_cpm"] == 12.50
        assert deal["deal_type"] == "PG"
        assert deal["media_type"] == "DIGITAL"

    def test_multiple_valid_rows(self, tmp_path):
        """Parse multiple valid deal rows."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm", "deal_type", "media_type"],
                ["DEAL-001", "Deal A", "ESPN", "10.00", "PG", "DIGITAL"],
                ["DEAL-002", "Deal B", "NBCU", "15.00", "PD", "CTV"],
                ["DEAL-003", "Deal C", "Fox", "8.00", "PA", "DIGITAL"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)

        assert result.total_rows == 3
        assert result.successful == 3
        assert result.failed == 0
        assert len(result.deals) == 3

    def test_path_object_accepted(self, tmp_path):
        """Accept pathlib.Path as well as str."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller"],
                ["DEAL-001", "Test", "ESPN"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(Path(path))
        assert result.successful == 1

    def test_default_values_applied(self, tmp_path):
        """Default seller_url and product_id are applied when not in CSV."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller"],
                ["DEAL-001", "Test", "ESPN"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(
            path,
            default_seller_url="https://seller.example.com",
            default_product_id="imported-batch",
        )
        deal = result.deals[0]
        assert deal["seller_url"] == "https://seller.example.com"
        assert deal["product_id"] == "imported-batch"

    def test_currency_defaults_to_usd(self, tmp_path):
        """Currency defaults to USD when not specified."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller"],
                ["DEAL-001", "Test", "ESPN"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["currency"] == "USD"


# ---------------------------------------------------------------------------
# Column name variation handling
# ---------------------------------------------------------------------------


class TestColumnNameVariations:
    """Test case-insensitive and format-variant column name detection."""

    def test_case_insensitive_headers(self, tmp_path):
        """Column names are matched case-insensitively."""
        path = _write_csv(
            [
                ["Deal_ID", "Name", "SELLER", "CPM", "Deal_Type", "Media_Type"],
                ["DEAL-001", "Test", "ESPN", "10", "PG", "DIGITAL"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.successful == 1
        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        assert deal["display_name"] == "Test"

    def test_space_underscore_variants(self, tmp_path):
        """Handle 'Deal ID' vs 'deal_id' vs 'DealID'."""
        path = _write_csv(
            [
                ["Deal ID", "Deal Name", "Publisher", "Price"],
                ["DEAL-001", "Test", "ESPN", "10.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.successful == 1
        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        assert deal["display_name"] == "Test"
        assert deal["seller_org"] == "ESPN"
        assert deal["fixed_price_cpm"] == 10.00

    def test_dealid_no_separator(self, tmp_path):
        """Handle 'DealID' (no separator)."""
        path = _write_csv(
            [
                ["DealID", "Name", "Seller"],
                ["DEAL-001", "Test", "ESPN"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["seller_deal_id"] == "DEAL-001"

    def test_publisher_domain_column(self, tmp_path):
        """Map 'publisher_domain' to seller_domain."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "publisher_domain"],
                ["DEAL-001", "Test", "ESPN", "espn.com"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["seller_domain"] == "espn.com"

    def test_bid_floor_column(self, tmp_path):
        """Map 'floor' and 'bid_floor' to bid_floor_cpm."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "floor"],
                ["DEAL-001", "Test", "ESPN", "5.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["bid_floor_cpm"] == 5.00

    def test_channel_maps_to_media_type(self, tmp_path):
        """Map 'channel' column to media_type."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "channel"],
                ["DEAL-001", "Test", "ESPN", "CTV"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["media_type"] == "CTV"


# ---------------------------------------------------------------------------
# Custom column mapping override
# ---------------------------------------------------------------------------


class TestCustomColumnMapping:
    """Test user-provided column mapping overrides."""

    def test_custom_mapping_overrides_auto(self, tmp_path):
        """Custom mapping dict overrides auto-detected column names."""
        path = _write_csv(
            [
                ["my_deal_code", "friendly_name", "vendor", "rate"],
                ["DEAL-001", "Test", "ESPN", "10.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(
            path,
            column_mapping={
                "my_deal_code": "seller_deal_id",
                "friendly_name": "display_name",
                "vendor": "seller_org",
                "rate": "fixed_price_cpm",
            },
        )
        assert result.successful == 1
        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        assert deal["display_name"] == "Test"
        assert deal["seller_org"] == "ESPN"
        assert deal["fixed_price_cpm"] == 10.00

    def test_custom_mapping_partial_with_auto(self, tmp_path):
        """Custom mapping for some cols, auto-detect for the rest."""
        path = _write_csv(
            [
                ["my_id", "name", "seller"],
                ["DEAL-001", "Test", "ESPN"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(
            path,
            column_mapping={"my_id": "seller_deal_id"},
        )
        assert result.successful == 1
        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        # "name" and "seller" should still be auto-detected
        assert deal["display_name"] == "Test"
        assert deal["seller_org"] == "ESPN"


# ---------------------------------------------------------------------------
# Date normalization
# ---------------------------------------------------------------------------


class TestDateNormalization:
    """Test date parsing and normalization to ISO 8601."""

    def test_iso_format_passthrough(self, tmp_path):
        """YYYY-MM-DD passes through as-is."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "start_date", "end_date"],
                ["DEAL-001", "Test", "ESPN", "2026-01-15", "2026-06-30"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        deal = result.deals[0]
        assert deal["flight_start"] == "2026-01-15"
        assert deal["flight_end"] == "2026-06-30"

    def test_us_date_format(self, tmp_path):
        """MM/DD/YYYY is normalized to YYYY-MM-DD."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "start_date", "end_date"],
                ["DEAL-001", "Test", "ESPN", "01/15/2026", "06/30/2026"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        deal = result.deals[0]
        assert deal["flight_start"] == "2026-01-15"
        assert deal["flight_end"] == "2026-06-30"

    def test_short_year_format(self, tmp_path):
        """M/D/YY is normalized to YYYY-MM-DD."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "start_date"],
                ["DEAL-001", "Test", "ESPN", "1/5/26"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["flight_start"] == "2026-01-05"

    def test_flight_start_flight_end_columns(self, tmp_path):
        """Direct flight_start/flight_end columns are recognized."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "flight_start", "flight_end"],
                ["DEAL-001", "Test", "ESPN", "2026-03-01", "2026-12-31"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        deal = result.deals[0]
        assert deal["flight_start"] == "2026-03-01"
        assert deal["flight_end"] == "2026-12-31"


# ---------------------------------------------------------------------------
# Deal type normalization
# ---------------------------------------------------------------------------


class TestDealTypeNormalization:
    """Test normalization of deal type values."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("PG", "PG"),
            ("pg", "PG"),
            ("Pg", "PG"),
            ("Programmatic Guaranteed", "PG"),
            ("programmatic guaranteed", "PG"),
            ("PD", "PD"),
            ("Preferred Deal", "PD"),
            ("preferred deal", "PD"),
            ("PA", "PA"),
            ("Private Auction", "PA"),
            ("private auction", "PA"),
            ("OPEN_AUCTION", "OPEN_AUCTION"),
            ("Open Auction", "OPEN_AUCTION"),
            ("open auction", "OPEN_AUCTION"),
            ("UPFRONT", "UPFRONT"),
            ("upfront", "UPFRONT"),
            ("Upfront", "UPFRONT"),
            ("SCATTER", "SCATTER"),
            ("scatter", "SCATTER"),
            ("Scatter", "SCATTER"),
        ],
    )
    def test_deal_type_normalization(self, tmp_path, input_val, expected):
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type"],
                ["DEAL-001", "Test", "ESPN", input_val],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["deal_type"] == expected


# ---------------------------------------------------------------------------
# Media type normalization
# ---------------------------------------------------------------------------


class TestMediaTypeNormalization:
    """Test normalization of media type values."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("DIGITAL", "DIGITAL"),
            ("digital", "DIGITAL"),
            ("Display", "DIGITAL"),
            ("display", "DIGITAL"),
            ("CTV", "CTV"),
            ("ctv", "CTV"),
            ("Connected TV", "CTV"),
            ("connected tv", "CTV"),
            ("LINEAR_TV", "LINEAR_TV"),
            ("Linear TV", "LINEAR_TV"),
            ("linear tv", "LINEAR_TV"),
            ("AUDIO", "AUDIO"),
            ("audio", "AUDIO"),
            ("Audio", "AUDIO"),
            ("DOOH", "DOOH"),
            ("dooh", "DOOH"),
        ],
    )
    def test_media_type_normalization(self, tmp_path, input_val, expected):
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "media_type"],
                ["DEAL-001", "Test", "ESPN", input_val],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["media_type"] == expected


# ---------------------------------------------------------------------------
# Price parsing
# ---------------------------------------------------------------------------


class TestPriceParsing:
    """Test price value parsing with various formats."""

    def test_plain_number(self, tmp_path):
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm"],
                ["DEAL-001", "Test", "ESPN", "12.50"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["fixed_price_cpm"] == 12.50

    def test_dollar_sign(self, tmp_path):
        """Parse '$12.50' -> 12.50."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm"],
                ["DEAL-001", "Test", "ESPN", "$12.50"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["fixed_price_cpm"] == 12.50

    def test_commas_in_number(self, tmp_path):
        """Parse '1,234.56' -> 1234.56."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm"],
                ["DEAL-001", "Test", "ESPN", "1,234.56"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["fixed_price_cpm"] == 1234.56

    def test_dollar_and_commas(self, tmp_path):
        """Parse '$1,234.56' -> 1234.56."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm"],
                ["DEAL-001", "Test", "ESPN", "$1,234.56"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["fixed_price_cpm"] == 1234.56

    def test_floor_price_parsing(self, tmp_path):
        """Floor price also handles $ and commas."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "floor"],
                ["DEAL-001", "Test", "ESPN", "$5.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["bid_floor_cpm"] == 5.00


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class TestValidationErrors:
    """Test validation of required fields and invalid values."""

    def test_missing_deal_id_and_name(self, tmp_path):
        """Row without deal_id or name fails validation."""
        path = _write_csv(
            [
                ["seller", "cpm"],
                ["ESPN", "10.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.failed == 1
        assert result.successful == 0
        assert len(result.errors) == 1
        assert result.errors[0].field == "seller_deal_id/display_name"

    def test_missing_seller_info(self, tmp_path):
        """Row with deal_id but no seller info fails validation."""
        path = _write_csv(
            [
                ["deal_id", "name", "cpm"],
                ["DEAL-001", "Test", "10.00"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.failed == 1
        assert len(result.errors) == 1
        assert "seller" in result.errors[0].field.lower()

    def test_invalid_deal_type(self, tmp_path):
        """Invalid deal_type value produces validation error."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type"],
                ["DEAL-001", "Test", "ESPN", "INVALID_TYPE"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.failed == 1
        assert len(result.errors) == 1
        assert result.errors[0].field == "deal_type"
        assert "INVALID_TYPE" in result.errors[0].value

    def test_invalid_media_type(self, tmp_path):
        """Invalid media_type value produces validation error."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "media_type"],
                ["DEAL-001", "Test", "ESPN", "HOLOGRAM"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.failed == 1
        assert result.errors[0].field == "media_type"


# ---------------------------------------------------------------------------
# Mixed valid/invalid rows
# ---------------------------------------------------------------------------


class TestMixedRows:
    """Test files with both valid and invalid rows."""

    def test_mixed_valid_and_invalid(self, tmp_path):
        """Some rows succeed, some fail; both reported correctly."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type", "media_type"],
                ["DEAL-001", "Good Deal", "ESPN", "PG", "DIGITAL"],
                ["DEAL-002", "Bad Type", "ESPN", "INVALID", "DIGITAL"],
                ["DEAL-003", "Good Deal 2", "Fox", "PD", "CTV"],
                ["DEAL-004", "Bad Media", "NBCU", "PG", "HOLOGRAM"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)

        assert result.total_rows == 4
        assert result.successful == 2
        assert result.failed == 2
        assert len(result.deals) == 2
        assert len(result.errors) == 2

    def test_error_row_numbers_are_correct(self, tmp_path):
        """Error objects reference the correct CSV row number (1-indexed, after header)."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type"],
                ["DEAL-001", "Good", "ESPN", "PG"],
                ["DEAL-002", "Bad", "ESPN", "INVALID"],
                ["DEAL-003", "Good", "Fox", "PD"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert len(result.errors) == 1
        # Row 2 in the data (1-indexed), which is CSV line 3 (header + 2 data rows)
        assert result.errors[0].row_number == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test empty files, header-only files, and other edge cases."""

    def test_empty_csv(self, tmp_path):
        """Empty file returns zero counts and no errors."""
        path = _write_csv([], tmpdir=str(tmp_path))
        result = parse_csv_deals(path)
        assert result.total_rows == 0
        assert result.successful == 0
        assert result.failed == 0
        assert result.deals == []
        assert result.errors == []

    def test_header_only_csv(self, tmp_path):
        """CSV with only headers returns zero counts."""
        path = _write_csv(
            [["deal_id", "name", "seller"]],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.total_rows == 0
        assert result.successful == 0
        assert result.deals == []

    def test_whitespace_in_values_stripped(self, tmp_path):
        """Leading/trailing whitespace in values is stripped."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller"],
                ["  DEAL-001  ", "  Test Deal  ", "  ESPN  "],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        deal = result.deals[0]
        assert deal["seller_deal_id"] == "DEAL-001"
        assert deal["display_name"] == "Test Deal"
        assert deal["seller_org"] == "ESPN"

    def test_empty_optional_fields(self, tmp_path):
        """Empty optional field values result in None."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "cpm", "media_type"],
                ["DEAL-001", "Test", "ESPN", "", ""],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.successful == 1
        deal = result.deals[0]
        assert deal.get("fixed_price_cpm") is None
        assert deal.get("media_type") is None

    def test_duplicate_deal_ids_skipped(self, tmp_path):
        """Duplicate seller_deal_id values within the file are skipped."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller"],
                ["DEAL-001", "First", "ESPN"],
                ["DEAL-001", "Duplicate", "ESPN"],
                ["DEAL-002", "Second", "Fox"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.successful == 2
        assert result.skipped == 1
        assert len(result.deals) == 2

    def test_currency_column_override(self, tmp_path):
        """Currency column value overrides the USD default."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "currency"],
                ["DEAL-001", "Test", "ESPN", "EUR"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["currency"] == "EUR"


# ---------------------------------------------------------------------------
# Import result counts
# ---------------------------------------------------------------------------


class TestImportResultCounts:
    """Verify ImportResult fields are populated correctly."""

    def test_counts_add_up(self, tmp_path):
        """total_rows == successful + failed + skipped."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type"],
                ["DEAL-001", "Good", "ESPN", "PG"],
                ["DEAL-002", "Bad", "ESPN", "INVALID"],
                ["DEAL-001", "Dup", "ESPN", "PG"],  # duplicate
                ["DEAL-003", "Good2", "Fox", "PD"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.total_rows == 4
        assert result.successful + result.failed + result.skipped == result.total_rows

    def test_import_error_dataclass_fields(self, tmp_path):
        """ImportError has row_number, field, value, message."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "deal_type"],
                ["DEAL-001", "Bad", "ESPN", "INVALID"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        err = result.errors[0]
        assert isinstance(err.row_number, int)
        assert isinstance(err.field, str)
        assert isinstance(err.value, str)
        assert isinstance(err.message, str)


# ---------------------------------------------------------------------------
# Additional schema field mapping
# ---------------------------------------------------------------------------


class TestAdditionalFields:
    """Test mapping of less-common but supported schema fields."""

    def test_impressions_column(self, tmp_path):
        """Map 'impressions' column to impressions field."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "impressions"],
                ["DEAL-001", "Test", "ESPN", "1000000"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["impressions"] == 1000000

    def test_description_column(self, tmp_path):
        """Map 'description' column."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "description"],
                ["DEAL-001", "Test", "ESPN", "Premium sports inventory"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["description"] == "Premium sports inventory"

    def test_buyer_org_column(self, tmp_path):
        """Map 'advertiser' / 'buyer' to buyer_org."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "advertiser"],
                ["DEAL-001", "Test", "ESPN", "QuickMeal Inc"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["buyer_org"] == "QuickMeal Inc"

    def test_geo_targets_column(self, tmp_path):
        """Map 'geo' / 'geography' to geo_targets."""
        path = _write_csv(
            [
                ["deal_id", "name", "seller", "geo"],
                ["DEAL-001", "Test", "ESPN", "US,CA"],
            ],
            tmpdir=str(tmp_path),
        )
        result = parse_csv_deals(path)
        assert result.deals[0]["geo_targets"] == "US,CA"
