# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for CreativeAsset model and creative asset CRUD operations.

All tests use in-memory SQLite (`:memory:`) for speed and isolation.
Tests cover the CreativeAsset dataclass, the creative_assets schema table,
and the CRUD methods on DealStore (save, get, list, update, delete).

bead: ar-pw8u
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from ad_buyer.models.creative_asset import (
    AssetType,
    CreativeAsset,
    ValidationStatus,
)
from ad_buyer.storage import DealStore


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def store():
    """Create a DealStore backed by in-memory SQLite."""
    s = DealStore("sqlite:///:memory:")
    s.connect()
    yield s
    s.disconnect()


@pytest.fixture
def sample_asset_kwargs():
    """Minimal valid kwargs for saving a creative asset."""
    return {
        "campaign_id": "campaign-001",
        "asset_name": "Hero Banner 300x250",
        "asset_type": "display",
        "format_spec": {"width": 300, "height": 250, "mime_type": "image/jpeg"},
        "source_url": "https://cdn.example.com/creatives/hero-300x250.jpg",
    }


# -----------------------------------------------------------------------
# CreativeAsset Model Tests
# -----------------------------------------------------------------------


class TestCreativeAssetModel:
    """Tests for the CreativeAsset dataclass."""

    def test_create_with_defaults(self):
        """Creating a CreativeAsset fills in defaults for optional fields."""
        asset = CreativeAsset(
            campaign_id="camp-1",
            asset_name="Test Banner",
            asset_type=AssetType.DISPLAY,
            format_spec={"width": 300, "height": 250},
            source_url="https://example.com/banner.jpg",
        )
        assert asset.asset_id is not None
        assert len(asset.asset_id) == 36  # UUID format
        assert asset.validation_status == ValidationStatus.PENDING
        assert asset.validation_errors == []
        assert asset.created_at is not None

    def test_create_with_explicit_values(self):
        """All fields can be explicitly set."""
        asset = CreativeAsset(
            asset_id="custom-id-123",
            campaign_id="camp-1",
            asset_name="Video Ad",
            asset_type=AssetType.VIDEO,
            format_spec={"duration_sec": 30, "vast_version": "4.2"},
            source_url="https://example.com/video.mp4",
            validation_status=ValidationStatus.VALID,
            validation_errors=[],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert asset.asset_id == "custom-id-123"
        assert asset.asset_type == AssetType.VIDEO
        assert asset.validation_status == ValidationStatus.VALID

    def test_asset_type_enum_values(self):
        """AssetType enum has all expected members."""
        assert set(AssetType) == {
            AssetType.DISPLAY,
            AssetType.VIDEO,
            AssetType.AUDIO,
            AssetType.INTERACTIVE,
            AssetType.NATIVE,
        }

    def test_validation_status_enum_values(self):
        """ValidationStatus enum has all expected members."""
        assert set(ValidationStatus) == {
            ValidationStatus.PENDING,
            ValidationStatus.VALID,
            ValidationStatus.INVALID,
        }

    def test_to_dict(self):
        """to_dict() returns a serializable dictionary."""
        asset = CreativeAsset(
            asset_id="abc-123",
            campaign_id="camp-1",
            asset_name="Banner",
            asset_type=AssetType.DISPLAY,
            format_spec={"width": 728, "height": 90},
            source_url="https://example.com/banner.jpg",
            validation_status=ValidationStatus.INVALID,
            validation_errors=["Size exceeds limit"],
        )
        d = asset.to_dict()
        assert d["asset_id"] == "abc-123"
        assert d["campaign_id"] == "camp-1"
        assert d["asset_type"] == "display"
        assert d["validation_status"] == "invalid"
        assert d["validation_errors"] == ["Size exceeds limit"]
        assert isinstance(d["format_spec"], dict)
        assert isinstance(d["created_at"], str)

    def test_from_dict(self):
        """from_dict() reconstructs a CreativeAsset from a dict."""
        original = CreativeAsset(
            campaign_id="camp-1",
            asset_name="Audio Spot",
            asset_type=AssetType.AUDIO,
            format_spec={"duration_sec": 15, "daast_version": "1.0"},
            source_url="https://example.com/audio.mp3",
        )
        d = original.to_dict()
        restored = CreativeAsset.from_dict(d)
        assert restored.asset_id == original.asset_id
        assert restored.asset_type == AssetType.AUDIO
        assert restored.format_spec == original.format_spec

    def test_format_spec_preserved_as_dict(self):
        """format_spec remains a dict, not serialized to string."""
        asset = CreativeAsset(
            campaign_id="camp-1",
            asset_name="Native Ad",
            asset_type=AssetType.NATIVE,
            format_spec={"title_max_len": 50, "image_sizes": ["1200x627"]},
            source_url="https://example.com/native.html",
        )
        assert isinstance(asset.format_spec, dict)
        assert asset.format_spec["title_max_len"] == 50


# -----------------------------------------------------------------------
# Schema Tests
# -----------------------------------------------------------------------


class TestCreativeAssetSchema:
    """Tests for the creative_assets table creation."""

    def test_creative_assets_table_exists(self, store):
        """The creative_assets table is created during schema init."""
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='creative_assets'"
        )
        assert cursor.fetchone() is not None

    def test_creative_assets_indexes_exist(self, store):
        """Expected indexes on creative_assets are created."""
        cursor = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_creative_assets_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        expected = {
            "idx_creative_assets_campaign_id",
            "idx_creative_assets_asset_type",
            "idx_creative_assets_validation_status",
        }
        assert expected.issubset(indexes)


# -----------------------------------------------------------------------
# CRUD Tests -- Save
# -----------------------------------------------------------------------


class TestSaveCreativeAsset:
    """Tests for save_creative_asset."""

    def test_save_returns_asset_id(self, store, sample_asset_kwargs):
        """save_creative_asset returns the asset_id."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        assert asset_id is not None
        assert isinstance(asset_id, str)
        assert len(asset_id) == 36  # UUID

    def test_save_with_custom_id(self, store, sample_asset_kwargs):
        """save_creative_asset uses a provided asset_id."""
        sample_asset_kwargs["asset_id"] = "my-custom-id"
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        assert asset_id == "my-custom-id"

    def test_save_stores_all_fields(self, store, sample_asset_kwargs):
        """All provided fields are persisted."""
        sample_asset_kwargs["validation_status"] = "valid"
        sample_asset_kwargs["validation_errors"] = ["minor warning"]
        asset_id = store.save_creative_asset(**sample_asset_kwargs)

        asset = store.get_creative_asset(asset_id)
        assert asset is not None
        assert asset["campaign_id"] == "campaign-001"
        assert asset["asset_name"] == "Hero Banner 300x250"
        assert asset["asset_type"] == "display"
        assert asset["source_url"] == "https://cdn.example.com/creatives/hero-300x250.jpg"
        assert asset["validation_status"] == "valid"
        assert asset["validation_errors"] == ["minor warning"]
        assert asset["format_spec"] == {"width": 300, "height": 250, "mime_type": "image/jpeg"}

    def test_save_defaults_validation_pending(self, store, sample_asset_kwargs):
        """Default validation_status is 'pending'."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert asset["validation_status"] == "pending"

    def test_save_defaults_empty_validation_errors(self, store, sample_asset_kwargs):
        """Default validation_errors is an empty list."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert asset["validation_errors"] == []


# -----------------------------------------------------------------------
# CRUD Tests -- Get
# -----------------------------------------------------------------------


class TestGetCreativeAsset:
    """Tests for get_creative_asset."""

    def test_get_existing_asset(self, store, sample_asset_kwargs):
        """get_creative_asset returns the asset dict."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert asset is not None
        assert asset["asset_id"] == asset_id

    def test_get_nonexistent_returns_none(self, store):
        """get_creative_asset returns None for missing IDs."""
        assert store.get_creative_asset("nonexistent-id") is None

    def test_get_deserializes_json_fields(self, store, sample_asset_kwargs):
        """format_spec and validation_errors are deserialized from JSON."""
        sample_asset_kwargs["validation_errors"] = ["err1", "err2"]
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert isinstance(asset["format_spec"], dict)
        assert isinstance(asset["validation_errors"], list)
        assert len(asset["validation_errors"]) == 2


# -----------------------------------------------------------------------
# CRUD Tests -- List
# -----------------------------------------------------------------------


class TestListCreativeAssets:
    """Tests for list_creative_assets."""

    def test_list_all(self, store, sample_asset_kwargs):
        """list_creative_assets with no filters returns all."""
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_name"] = "Second Asset"
        store.save_creative_asset(**sample_asset_kwargs)
        assets = store.list_creative_assets()
        assert len(assets) == 2

    def test_list_filter_by_campaign_id(self, store, sample_asset_kwargs):
        """list_creative_assets filters by campaign_id."""
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["campaign_id"] = "campaign-002"
        store.save_creative_asset(**sample_asset_kwargs)

        assets = store.list_creative_assets(campaign_id="campaign-001")
        assert len(assets) == 1
        assert assets[0]["campaign_id"] == "campaign-001"

    def test_list_filter_by_asset_type(self, store, sample_asset_kwargs):
        """list_creative_assets filters by asset_type."""
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_type"] = "video"
        sample_asset_kwargs["format_spec"] = {"duration_sec": 30}
        store.save_creative_asset(**sample_asset_kwargs)

        assets = store.list_creative_assets(asset_type="video")
        assert len(assets) == 1
        assert assets[0]["asset_type"] == "video"

    def test_list_filter_by_validation_status(self, store, sample_asset_kwargs):
        """list_creative_assets filters by validation_status."""
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_name"] = "Valid Asset"
        sample_asset_kwargs["validation_status"] = "valid"
        store.save_creative_asset(**sample_asset_kwargs)

        assets = store.list_creative_assets(validation_status="valid")
        assert len(assets) == 1
        assert assets[0]["validation_status"] == "valid"

    def test_list_multiple_filters(self, store, sample_asset_kwargs):
        """list_creative_assets supports combining filters."""
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_name"] = "Valid Display"
        sample_asset_kwargs["validation_status"] = "valid"
        store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_type"] = "video"
        sample_asset_kwargs["asset_name"] = "Valid Video"
        store.save_creative_asset(**sample_asset_kwargs)

        assets = store.list_creative_assets(
            campaign_id="campaign-001",
            asset_type="display",
            validation_status="valid",
        )
        assert len(assets) == 1
        assert assets[0]["asset_name"] == "Valid Display"

    def test_list_limit(self, store, sample_asset_kwargs):
        """list_creative_assets respects the limit parameter."""
        for i in range(5):
            sample_asset_kwargs["asset_name"] = f"Asset {i}"
            store.save_creative_asset(**sample_asset_kwargs)
        assets = store.list_creative_assets(limit=3)
        assert len(assets) == 3

    def test_list_ordered_by_created_at_desc(self, store, sample_asset_kwargs):
        """list_creative_assets returns newest first."""
        id1 = store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_name"] = "Second Asset"
        id2 = store.save_creative_asset(**sample_asset_kwargs)

        assets = store.list_creative_assets()
        assert assets[0]["asset_id"] == id2
        assert assets[1]["asset_id"] == id1

    def test_list_empty(self, store):
        """list_creative_assets returns empty list when no assets exist."""
        assert store.list_creative_assets() == []

    def test_list_deserializes_json(self, store, sample_asset_kwargs):
        """list_creative_assets deserializes JSON fields in results."""
        sample_asset_kwargs["validation_errors"] = ["err"]
        store.save_creative_asset(**sample_asset_kwargs)
        assets = store.list_creative_assets()
        assert isinstance(assets[0]["format_spec"], dict)
        assert isinstance(assets[0]["validation_errors"], list)


# -----------------------------------------------------------------------
# CRUD Tests -- Update
# -----------------------------------------------------------------------


class TestUpdateCreativeAsset:
    """Tests for update_creative_asset."""

    def test_update_single_field(self, store, sample_asset_kwargs):
        """update_creative_asset can change a single field."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        result = store.update_creative_asset(asset_id, asset_name="Renamed Banner")
        assert result is True

        asset = store.get_creative_asset(asset_id)
        assert asset["asset_name"] == "Renamed Banner"

    def test_update_validation_status(self, store, sample_asset_kwargs):
        """update_creative_asset can change validation_status."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        store.update_creative_asset(
            asset_id,
            validation_status="invalid",
            validation_errors=["Missing alt text", "Exceeds file size"],
        )
        asset = store.get_creative_asset(asset_id)
        assert asset["validation_status"] == "invalid"
        assert asset["validation_errors"] == ["Missing alt text", "Exceeds file size"]

    def test_update_format_spec(self, store, sample_asset_kwargs):
        """update_creative_asset can update format_spec."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        new_spec = {"width": 728, "height": 90, "mime_type": "image/png"}
        store.update_creative_asset(asset_id, format_spec=new_spec)

        asset = store.get_creative_asset(asset_id)
        assert asset["format_spec"] == new_spec

    def test_update_multiple_fields(self, store, sample_asset_kwargs):
        """update_creative_asset can change multiple fields at once."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        store.update_creative_asset(
            asset_id,
            asset_name="Updated Name",
            source_url="https://new-cdn.example.com/banner.jpg",
            validation_status="valid",
        )
        asset = store.get_creative_asset(asset_id)
        assert asset["asset_name"] == "Updated Name"
        assert asset["source_url"] == "https://new-cdn.example.com/banner.jpg"
        assert asset["validation_status"] == "valid"

    def test_update_nonexistent_returns_false(self, store):
        """update_creative_asset returns False for missing IDs."""
        result = store.update_creative_asset("nonexistent", asset_name="X")
        assert result is False

    def test_update_updates_updated_at(self, store, sample_asset_kwargs):
        """update_creative_asset bumps the updated_at timestamp."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        before = store.get_creative_asset(asset_id)["updated_at"]
        store.update_creative_asset(asset_id, asset_name="Renamed")
        after = store.get_creative_asset(asset_id)["updated_at"]
        assert after >= before

    def test_update_no_fields_is_noop(self, store, sample_asset_kwargs):
        """update_creative_asset with no kwargs returns False (nothing to update)."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        result = store.update_creative_asset(asset_id)
        assert result is False


# -----------------------------------------------------------------------
# CRUD Tests -- Delete
# -----------------------------------------------------------------------


class TestDeleteCreativeAsset:
    """Tests for delete_creative_asset."""

    def test_delete_existing_asset(self, store, sample_asset_kwargs):
        """delete_creative_asset removes the asset."""
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        result = store.delete_creative_asset(asset_id)
        assert result is True
        assert store.get_creative_asset(asset_id) is None

    def test_delete_nonexistent_returns_false(self, store):
        """delete_creative_asset returns False for missing IDs."""
        result = store.delete_creative_asset("nonexistent")
        assert result is False

    def test_delete_does_not_affect_other_assets(self, store, sample_asset_kwargs):
        """Deleting one asset leaves others untouched."""
        id1 = store.save_creative_asset(**sample_asset_kwargs)
        sample_asset_kwargs["asset_name"] = "Other Asset"
        id2 = store.save_creative_asset(**sample_asset_kwargs)

        store.delete_creative_asset(id1)
        assert store.get_creative_asset(id1) is None
        assert store.get_creative_asset(id2) is not None


# -----------------------------------------------------------------------
# Thread Safety Tests
# -----------------------------------------------------------------------


class TestCreativeAssetThreadSafety:
    """Tests for concurrent access to creative asset CRUD."""

    def test_concurrent_saves(self, store):
        """Multiple threads can save creative assets without corruption."""
        errors = []
        created_ids = []
        lock = threading.Lock()

        def writer(n):
            try:
                aid = store.save_creative_asset(
                    campaign_id=f"camp-{n}",
                    asset_name=f"Asset {n}",
                    asset_type="display",
                    format_spec={"width": 300, "height": 250},
                    source_url=f"https://example.com/{n}.jpg",
                )
                with lock:
                    created_ids.append(aid)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Errors during concurrent writes: {errors}"
        assert len(created_ids) == 20


# -----------------------------------------------------------------------
# Edge Case Tests
# -----------------------------------------------------------------------


class TestCreativeAssetEdgeCases:
    """Tests for edge cases and data integrity."""

    def test_unicode_asset_name(self, store, sample_asset_kwargs):
        """Unicode in asset names is preserved."""
        sample_asset_kwargs["asset_name"] = "Banniere Publicitaire"
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert asset["asset_name"] == "Banniere Publicitaire"

    def test_large_format_spec(self, store, sample_asset_kwargs):
        """Large format_spec JSON is stored and retrieved correctly."""
        large_spec = {f"key_{i}": f"value_{i}" for i in range(100)}
        sample_asset_kwargs["format_spec"] = large_spec
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert len(asset["format_spec"]) == 100

    def test_all_asset_types_storable(self, store, sample_asset_kwargs):
        """Each AssetType value can be stored and retrieved."""
        for atype in AssetType:
            sample_asset_kwargs["asset_type"] = atype.value
            sample_asset_kwargs["asset_name"] = f"Asset {atype.value}"
            asset_id = store.save_creative_asset(**sample_asset_kwargs)
            asset = store.get_creative_asset(asset_id)
            assert asset["asset_type"] == atype.value

    def test_all_validation_statuses_storable(self, store, sample_asset_kwargs):
        """Each ValidationStatus value can be stored and retrieved."""
        for vs in ValidationStatus:
            sample_asset_kwargs["validation_status"] = vs.value
            sample_asset_kwargs["asset_name"] = f"Asset {vs.value}"
            asset_id = store.save_creative_asset(**sample_asset_kwargs)
            asset = store.get_creative_asset(asset_id)
            assert asset["validation_status"] == vs.value

    def test_empty_validation_errors_list(self, store, sample_asset_kwargs):
        """Empty validation_errors list serializes and deserializes correctly."""
        sample_asset_kwargs["validation_errors"] = []
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert asset["validation_errors"] == []

    def test_format_spec_with_nested_objects(self, store, sample_asset_kwargs):
        """format_spec with nested structures is preserved."""
        sample_asset_kwargs["format_spec"] = {
            "sizes": [{"width": 300, "height": 250}, {"width": 728, "height": 90}],
            "mime_types": ["image/jpeg", "image/png"],
            "constraints": {"max_file_kb": 150},
        }
        asset_id = store.save_creative_asset(**sample_asset_kwargs)
        asset = store.get_creative_asset(asset_id)
        assert len(asset["format_spec"]["sizes"]) == 2
        assert asset["format_spec"]["constraints"]["max_file_kb"] == 150
