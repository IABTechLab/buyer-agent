# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CSV deal import parser for DealJockey.

Reads CSV files containing deal data and maps them to the v2 deal library
schema.  This is a pure function -- it does NOT call DealStore.save_deal()
or emit events.  The caller (DealJockey agent or a higher-level import
tool) is responsible for:

1. Calling ``parse_csv_deals()`` to get parsed deal dicts.
2. Saving each deal via ``DealStore.save_deal()`` or equivalent.
3. Emitting ``EventType.DEAL_IMPORTED`` for each successfully imported deal.
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ImportError:
    """A validation error for a single CSV row.

    Attributes:
        row_number: 1-indexed row number within the data rows (excludes header).
        field: Schema field name that failed validation.
        value: The raw value that was rejected.
        message: Human-readable description of the problem.
    """

    row_number: int
    field: str
    value: str
    message: str


@dataclass
class ImportResult:
    """Result of a CSV deal import operation.

    Attributes:
        deals: Successfully parsed deals as dicts ready for DealStore.
        errors: Per-row validation errors.
        total_rows: Number of data rows in the file (excludes header).
        successful: Number of rows that parsed successfully.
        failed: Number of rows that failed validation.
        skipped: Number of rows skipped (e.g. duplicate deal_ids).
    """

    deals: list[dict[str, Any]] = field(default_factory=list)
    errors: list[ImportError] = field(default_factory=list)
    total_rows: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------

# Maps common CSV header names (lowercase, stripped) to schema field names.
COLUMN_MAPPINGS: dict[str, str] = {
    # deal_id variations
    "deal_id": "seller_deal_id",
    "deal id": "seller_deal_id",
    "dealid": "seller_deal_id",
    "deal_code": "seller_deal_id",
    "seller_deal_id": "seller_deal_id",
    # name / display name variations
    "name": "display_name",
    "deal_name": "display_name",
    "deal name": "display_name",
    "display_name": "display_name",
    "dealname": "display_name",
    # seller / publisher variations
    "publisher": "seller_org",
    "seller": "seller_org",
    "seller_org": "seller_org",
    "publisher_name": "seller_org",
    "seller_name": "seller_org",
    # seller domain
    "publisher_domain": "seller_domain",
    "seller_domain": "seller_domain",
    "domain": "seller_domain",
    # pricing -- fixed CPM
    "cpm": "fixed_price_cpm",
    "price": "fixed_price_cpm",
    "fixed_price_cpm": "fixed_price_cpm",
    "fixed_cpm": "fixed_price_cpm",
    "rate": "fixed_price_cpm",
    # pricing -- bid floor
    "floor": "bid_floor_cpm",
    "bid_floor": "bid_floor_cpm",
    "bid_floor_cpm": "bid_floor_cpm",
    "floor_cpm": "bid_floor_cpm",
    # deal type
    "deal_type": "deal_type",
    "type": "deal_type",
    "dealtype": "deal_type",
    # media type / channel
    "media_type": "media_type",
    "channel": "media_type",
    "mediatype": "media_type",
    "media type": "media_type",
    # dates
    "start_date": "flight_start",
    "end_date": "flight_end",
    "flight_start": "flight_start",
    "flight_end": "flight_end",
    "start date": "flight_start",
    "end date": "flight_end",
    # impressions
    "impressions": "impressions",
    "volume": "impressions",
    "estimated_volume": "estimated_volume",
    # buyer / advertiser
    "advertiser": "buyer_org",
    "buyer": "buyer_org",
    "buyer_org": "buyer_org",
    "advertiser_name": "buyer_org",
    # description
    "description": "description",
    # currency
    "currency": "currency",
    # geo
    "geo": "geo_targets",
    "geography": "geo_targets",
    "geo_targets": "geo_targets",
    # formats
    "formats": "formats",
    "format": "formats",
    # content categories
    "content_categories": "content_categories",
    "category": "content_categories",
    "categories": "content_categories",
    # audience
    "audience_segments": "audience_segments",
    "audience": "audience_segments",
}

# ---------------------------------------------------------------------------
# Normalization tables
# ---------------------------------------------------------------------------

VALID_DEAL_TYPES = {"PG", "PD", "PA", "OPEN_AUCTION", "UPFRONT", "SCATTER"}

_DEAL_TYPE_ALIASES: dict[str, str] = {
    "pg": "PG",
    "programmatic guaranteed": "PG",
    "pd": "PD",
    "preferred deal": "PD",
    "preferred": "PD",
    "pa": "PA",
    "private auction": "PA",
    "open_auction": "OPEN_AUCTION",
    "open auction": "OPEN_AUCTION",
    "openauction": "OPEN_AUCTION",
    "upfront": "UPFRONT",
    "scatter": "SCATTER",
}

VALID_MEDIA_TYPES = {"DIGITAL", "CTV", "LINEAR_TV", "AUDIO", "DOOH"}

_MEDIA_TYPE_ALIASES: dict[str, str] = {
    "digital": "DIGITAL",
    "display": "DIGITAL",
    "ctv": "CTV",
    "connected tv": "CTV",
    "connected_tv": "CTV",
    "linear_tv": "LINEAR_TV",
    "linear tv": "LINEAR_TV",
    "lineartv": "LINEAR_TV",
    "audio": "AUDIO",
    "dooh": "DOOH",
}

# Fields that should be parsed as floats
_FLOAT_FIELDS = {"fixed_price_cpm", "bid_floor_cpm", "cpp", "guaranteed_grps", "fee_transparency"}

# Fields that should be parsed as ints
_INT_FIELDS = {"impressions", "estimated_volume"}

# Date fields
_DATE_FIELDS = {"flight_start", "flight_end"}

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_header(header: str) -> str:
    """Lowercase and strip a header for lookup in COLUMN_MAPPINGS."""
    return header.strip().lower()


def _parse_price(value: str) -> float | None:
    """Parse a price string, handling $, commas, whitespace.

    Examples:
        "$12.50" -> 12.50
        "1,234.56" -> 1234.56
        "$1,234.56" -> 1234.56
        "" -> None

    Returns:
        Parsed float or None if the value is empty or unparseable.
    """
    cleaned = value.strip()
    if not cleaned:
        return None
    # Remove dollar sign and commas
    cleaned = cleaned.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    """Parse an integer string, handling commas.

    Returns:
        Parsed int or None if the value is empty or unparseable.
    """
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _normalize_date(value: str) -> str | None:
    """Normalize date strings to ISO 8601 (YYYY-MM-DD).

    Supported input formats:
        - YYYY-MM-DD (passthrough)
        - MM/DD/YYYY
        - M/D/YYYY
        - MM/DD/YY
        - M/D/YY

    Returns:
        ISO date string or None if empty/unparseable.
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    # Already ISO format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        return cleaned

    # Try MM/DD/YYYY or M/D/YYYY
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _normalize_deal_type(value: str) -> str | None:
    """Normalize deal type to one of the valid values.

    Returns:
        Normalized deal type string, or None if empty.

    Raises:
        ValueError: If value is non-empty but not a recognized deal type.
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    # Direct match (case-insensitive)
    upper = cleaned.upper()
    if upper in VALID_DEAL_TYPES:
        return upper

    # Alias lookup
    lower = cleaned.lower()
    if lower in _DEAL_TYPE_ALIASES:
        return _DEAL_TYPE_ALIASES[lower]

    raise ValueError(f"Unrecognized deal type: {cleaned}")


def _normalize_media_type(value: str) -> str | None:
    """Normalize media type to one of the valid values.

    Returns:
        Normalized media type string, or None if empty.

    Raises:
        ValueError: If value is non-empty but not a recognized media type.
    """
    cleaned = value.strip()
    if not cleaned:
        return None

    # Direct match (case-insensitive)
    upper = cleaned.upper()
    if upper in VALID_MEDIA_TYPES:
        return upper

    # Alias lookup
    lower = cleaned.lower()
    if lower in _MEDIA_TYPE_ALIASES:
        return _MEDIA_TYPE_ALIASES[lower]

    raise ValueError(f"Unrecognized media type: {cleaned}")


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def _resolve_columns(
    headers: list[str],
    column_mapping: dict[str, str] | None,
) -> dict[int, str]:
    """Build a mapping from column index to schema field name.

    Custom mappings are applied first (case-insensitive match on the
    original header).  Remaining unmapped columns are auto-detected via
    ``COLUMN_MAPPINGS``.

    Args:
        headers: Raw CSV header row.
        column_mapping: Optional user-provided overrides
            (original_header -> schema_field).

    Returns:
        Dict mapping column index -> schema field name.  Columns that
        cannot be mapped are omitted.
    """
    index_map: dict[int, str] = {}
    mapped_indices: set[int] = set()

    # Pass 1: apply custom mapping (case-insensitive header match)
    if column_mapping:
        lower_custom = {k.strip().lower(): v for k, v in column_mapping.items()}
        for idx, hdr in enumerate(headers):
            key = hdr.strip().lower()
            if key in lower_custom:
                index_map[idx] = lower_custom[key]
                mapped_indices.add(idx)

    # Pass 2: auto-detect remaining columns
    for idx, hdr in enumerate(headers):
        if idx in mapped_indices:
            continue
        key = _normalize_header(hdr)
        if key in COLUMN_MAPPINGS:
            index_map[idx] = COLUMN_MAPPINGS[key]

    return index_map


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------


def _parse_row(
    row: list[str],
    col_map: dict[int, str],
    row_number: int,
    *,
    default_seller_url: str,
    default_product_id: str,
) -> tuple[dict[str, Any] | None, list[ImportError]]:
    """Parse a single CSV data row into a deal dict.

    Returns:
        (deal_dict, errors) -- deal_dict is None when validation fails.
    """
    errors: list[ImportError] = []

    # Build raw field values from the CSV row
    raw: dict[str, str] = {}
    for idx, field_name in col_map.items():
        if idx < len(row):
            raw[field_name] = row[idx].strip()

    # --- Normalize and build the deal dict ---
    deal: dict[str, Any] = {}

    # Identity fields
    deal["seller_deal_id"] = raw.get("seller_deal_id", "").strip() or None
    deal["display_name"] = raw.get("display_name", "").strip() or None

    # Require at least one of seller_deal_id or display_name
    if not deal["seller_deal_id"] and not deal["display_name"]:
        errors.append(
            ImportError(
                row_number=row_number,
                field="seller_deal_id/display_name",
                value="",
                message="At least one of deal_id or name is required to identify the deal.",
            )
        )
        return None, errors

    # Seller info (required)
    deal["seller_org"] = raw.get("seller_org", "").strip() or None
    deal["seller_domain"] = raw.get("seller_domain", "").strip() or None

    if not deal["seller_org"] and not deal["seller_domain"]:
        errors.append(
            ImportError(
                row_number=row_number,
                field="seller_org/seller_domain",
                value="",
                message="Seller information is required (seller/publisher name or domain).",
            )
        )
        return None, errors

    # Defaults
    deal["seller_url"] = default_seller_url
    deal["product_id"] = default_product_id
    deal["status"] = "imported"

    # Deal type normalization
    raw_deal_type = raw.get("deal_type", "").strip()
    if raw_deal_type:
        try:
            deal["deal_type"] = _normalize_deal_type(raw_deal_type)
        except ValueError:
            errors.append(
                ImportError(
                    row_number=row_number,
                    field="deal_type",
                    value=raw_deal_type,
                    message=(
                        f"Invalid deal type '{raw_deal_type}'. "
                        f"Must be one of: {', '.join(sorted(VALID_DEAL_TYPES))}"
                    ),
                )
            )
            return None, errors
    else:
        deal["deal_type"] = "PD"  # default

    # Media type normalization
    raw_media_type = raw.get("media_type", "").strip()
    if raw_media_type:
        try:
            deal["media_type"] = _normalize_media_type(raw_media_type)
        except ValueError:
            errors.append(
                ImportError(
                    row_number=row_number,
                    field="media_type",
                    value=raw_media_type,
                    message=(
                        f"Invalid media type '{raw_media_type}'. "
                        f"Must be one of: {', '.join(sorted(VALID_MEDIA_TYPES))}"
                    ),
                )
            )
            return None, errors
    else:
        deal["media_type"] = None

    # Float fields (prices, etc.)
    for field_name in _FLOAT_FIELDS:
        raw_val = raw.get(field_name, "").strip()
        if raw_val:
            parsed = _parse_price(raw_val)
            deal[field_name] = parsed
        else:
            deal[field_name] = None

    # Int fields
    for field_name in _INT_FIELDS:
        raw_val = raw.get(field_name, "").strip()
        if raw_val:
            parsed = _parse_int(raw_val)
            deal[field_name] = parsed
        else:
            deal[field_name] = None

    # Date fields
    for field_name in _DATE_FIELDS:
        raw_val = raw.get(field_name, "").strip()
        if raw_val:
            parsed = _normalize_date(raw_val)
            deal[field_name] = parsed
        else:
            deal[field_name] = None

    # Currency (default USD)
    raw_currency = raw.get("currency", "").strip()
    deal["currency"] = raw_currency.upper() if raw_currency else "USD"

    # String pass-through fields
    for field_name in (
        "description",
        "buyer_org",
        "buyer_id",
        "seller_id",
        "seller_type",
        "geo_targets",
        "formats",
        "content_categories",
        "audience_segments",
        "programs",
        "networks",
        "dayparts",
        "publisher_domains",
    ):
        raw_val = raw.get(field_name, "").strip()
        deal[field_name] = raw_val if raw_val else None

    return deal, errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_csv_deals(
    file_path: str | Path,
    *,
    column_mapping: dict[str, str] | None = None,
    default_seller_url: str = "",
    default_product_id: str = "imported",
    skip_header: bool = True,
) -> ImportResult:
    """Parse a CSV file of deals and return structured results.

    This is a pure function -- it reads the CSV and returns parsed deal
    dicts without touching any database or emitting events.  The caller
    should:

    1. Iterate ``result.deals`` and call ``DealStore.save_deal()`` for each.
    2. Emit ``EventType.DEAL_IMPORTED`` for each successfully saved deal.

    Args:
        file_path: Path to the CSV file.
        column_mapping: Optional dict mapping CSV column names to schema
            field names, overriding auto-detection.
        default_seller_url: Default ``seller_url`` for all imported deals
            (since CSV rarely contains full URLs).
        default_product_id: Default ``product_id`` for all imported deals.
        skip_header: Whether the first row is a header (default True).

    Returns:
        An ``ImportResult`` with parsed deals, errors, and counts.
    """
    file_path = Path(file_path)
    result = ImportResult()

    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return result

    # First row is headers
    if skip_header:
        headers = rows[0]
        data_rows = rows[1:]
    else:
        # If no header, we can't auto-map; require column_mapping
        headers = [str(i) for i in range(len(rows[0]))]
        data_rows = rows

    col_map = _resolve_columns(headers, column_mapping)

    if not col_map:
        logger.warning("No columns could be mapped to schema fields")
        return result

    result.total_rows = len(data_rows)

    # Track seen deal IDs for deduplication within the file
    seen_deal_ids: set[str] = set()

    for row_idx, row in enumerate(data_rows, start=1):
        # Skip completely empty rows
        if not any(cell.strip() for cell in row):
            result.total_rows -= 1
            continue

        deal, errors = _parse_row(
            row,
            col_map,
            row_number=row_idx,
            default_seller_url=default_seller_url,
            default_product_id=default_product_id,
        )

        if errors:
            result.errors.extend(errors)
            result.failed += 1
            continue

        # Deduplication by seller_deal_id
        deal_id = deal.get("seller_deal_id")
        if deal_id and deal_id in seen_deal_ids:
            result.skipped += 1
            continue

        if deal_id:
            seen_deal_ids.add(deal_id)

        result.deals.append(deal)
        result.successful += 1

    return result
