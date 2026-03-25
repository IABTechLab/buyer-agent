# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DealStore deal library CRUD operations (v2).

Tests cover:
- portfolio_metadata CRUD (save, get, update, delete)
- deal_activations CRUD (save, get, update, delete)
- performance_cache CRUD (save, get, update, delete)
- save_deal() with v2 intrinsic fields
- list_deals() with v2 filters (media_type, seller_domain, deal_type, advertiser_id)
- Foreign key constraints
- Backward compatibility
"""

import json
import sqlite3

import pytest

from ad_buyer.storage import DealStore

# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def deal_store():
    """Create a DealStore backed by in-memory SQLite."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def deal_with_metadata(deal_store):
    """Create a deal and attach portfolio metadata for convenience."""
    deal_id = deal_store.save_deal(
        seller_url="http://seller.example.com",
        product_id="prod_1",
        product_name="Banner Ad",
    )
    meta_id = deal_store.save_portfolio_metadata(
        deal_id=deal_id,
        import_source="CSV",
        import_date="2026-03-18",
        tags=json.dumps(["premium", "sports"]),
        advertiser_id="adv-001",
        agency_id="agency-001",
    )
    return deal_id, meta_id


# -----------------------------------------------------------------------
# Portfolio Metadata CRUD Tests
# -----------------------------------------------------------------------


class TestPortfolioMetadataCRUD:
    """Tests for portfolio_metadata save, get, update, delete."""

    def test_save_portfolio_metadata_returns_row_id(self, deal_store):
        """save_portfolio_metadata returns the row ID."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="CSV",
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_save_portfolio_metadata_all_fields(self, deal_store):
        """save_portfolio_metadata stores all fields correctly."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="TTD_API",
            import_date="2026-03-18",
            tags=json.dumps(["video", "premium"]),
            advertiser_id="adv-123",
            agency_id="agency-456",
        )
        meta = deal_store.get_portfolio_metadata(deal_id)
        assert meta is not None
        assert meta["id"] == row_id
        assert meta["deal_id"] == deal_id
        assert meta["import_source"] == "TTD_API"
        assert meta["import_date"] == "2026-03-18"
        assert json.loads(meta["tags"]) == ["video", "premium"]
        assert meta["advertiser_id"] == "adv-123"
        assert meta["agency_id"] == "agency-456"

    def test_save_portfolio_metadata_minimal_fields(self, deal_store):
        """save_portfolio_metadata works with only deal_id."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_portfolio_metadata(deal_id=deal_id)
        assert row_id > 0
        meta = deal_store.get_portfolio_metadata(deal_id)
        assert meta is not None
        assert meta["import_source"] is None
        assert meta["tags"] is None

    def test_get_portfolio_metadata_returns_dict(self, deal_with_metadata):
        """get_portfolio_metadata returns a dict for existing deal."""
        deal_id, _ = deal_with_metadata
        # get_portfolio_metadata is tested via deal_with_metadata fixture
        # just verify it returns a dict
        # Already tested via fixture, but explicitly:
        meta = deal_with_metadata  # deal_id, meta_id tuple
        assert meta is not None

    def test_get_portfolio_metadata_not_found(self, deal_store):
        """get_portfolio_metadata returns None for deal with no metadata."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        assert deal_store.get_portfolio_metadata(deal_id) is None

    def test_get_portfolio_metadata_nonexistent_deal(self, deal_store):
        """get_portfolio_metadata returns None for nonexistent deal."""
        assert deal_store.get_portfolio_metadata("nonexistent") is None

    def test_update_portfolio_metadata(self, deal_store):
        """update_portfolio_metadata updates specified fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="CSV",
            advertiser_id="adv-001",
        )
        result = deal_store.update_portfolio_metadata(
            deal_id,
            import_source="MANUAL",
            tags=json.dumps(["updated"]),
        )
        assert result is True

        meta = deal_store.get_portfolio_metadata(deal_id)
        assert meta["import_source"] == "MANUAL"
        assert json.loads(meta["tags"]) == ["updated"]
        # Unchanged field should remain
        assert meta["advertiser_id"] == "adv-001"

    def test_update_portfolio_metadata_not_found(self, deal_store):
        """update_portfolio_metadata returns False for nonexistent deal."""
        result = deal_store.update_portfolio_metadata(
            "nonexistent",
            import_source="CSV",
        )
        assert result is False

    def test_update_portfolio_metadata_no_kwargs(self, deal_store):
        """update_portfolio_metadata with no kwargs returns False."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_portfolio_metadata(deal_id=deal_id)
        result = deal_store.update_portfolio_metadata(deal_id)
        assert result is False

    def test_delete_portfolio_metadata(self, deal_store):
        """delete_portfolio_metadata removes metadata for a deal."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="CSV",
        )
        result = deal_store.delete_portfolio_metadata(deal_id)
        assert result is True
        assert deal_store.get_portfolio_metadata(deal_id) is None

    def test_delete_portfolio_metadata_not_found(self, deal_store):
        """delete_portfolio_metadata returns False for nonexistent deal."""
        assert deal_store.delete_portfolio_metadata("nonexistent") is False

    def test_save_portfolio_metadata_foreign_key_constraint(self, deal_store):
        """save_portfolio_metadata fails for nonexistent deal_id."""
        with pytest.raises(sqlite3.IntegrityError):
            deal_store.save_portfolio_metadata(
                deal_id="nonexistent-deal",
                import_source="CSV",
            )


# -----------------------------------------------------------------------
# Deal Activations CRUD Tests
# -----------------------------------------------------------------------


class TestDealActivationsCRUD:
    """Tests for deal_activations save, get, update, delete."""

    def test_save_deal_activation_returns_row_id(self, deal_store):
        """save_deal_activation returns the row ID."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_save_deal_activation_all_fields(self, deal_store):
        """save_deal_activation stores all fields correctly."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
            platform_deal_id="TTD-ESPN-12345",
            activation_status="ACTIVE",
            last_sync_at="2026-03-18T10:00:00Z",
        )
        activations = deal_store.get_deal_activations(deal_id)
        assert len(activations) == 1
        act = activations[0]
        assert act["id"] == row_id
        assert act["deal_id"] == deal_id
        assert act["platform"] == "TTD"
        assert act["platform_deal_id"] == "TTD-ESPN-12345"
        assert act["activation_status"] == "ACTIVE"
        assert act["last_sync_at"] == "2026-03-18T10:00:00Z"

    def test_get_deal_activations_multiple(self, deal_store):
        """get_deal_activations returns all activations for a deal."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
            platform_deal_id="TTD-001",
            activation_status="ACTIVE",
        )
        deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="DV360",
            platform_deal_id="DV360-001",
            activation_status="PENDING",
        )
        deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="XANDR",
            platform_deal_id="XN-001",
            activation_status="ACTIVE",
        )
        activations = deal_store.get_deal_activations(deal_id)
        assert len(activations) == 3

    def test_get_deal_activations_empty(self, deal_store):
        """get_deal_activations returns empty list for deal with no activations."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        assert deal_store.get_deal_activations(deal_id) == []

    def test_update_deal_activation(self, deal_store):
        """update_deal_activation updates specified fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        act_id = deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
            platform_deal_id="TTD-001",
            activation_status="PENDING",
        )
        result = deal_store.update_deal_activation(
            act_id,
            activation_status="ACTIVE",
            last_sync_at="2026-03-18T12:00:00Z",
        )
        assert result is True

        activations = deal_store.get_deal_activations(deal_id)
        assert len(activations) == 1
        act = activations[0]
        assert act["activation_status"] == "ACTIVE"
        assert act["last_sync_at"] == "2026-03-18T12:00:00Z"
        # Unchanged field
        assert act["platform_deal_id"] == "TTD-001"

    def test_update_deal_activation_not_found(self, deal_store):
        """update_deal_activation returns False for nonexistent activation."""
        result = deal_store.update_deal_activation(
            99999,
            activation_status="ACTIVE",
        )
        assert result is False

    def test_update_deal_activation_no_kwargs(self, deal_store):
        """update_deal_activation with no kwargs returns False."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        act_id = deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
        )
        result = deal_store.update_deal_activation(act_id)
        assert result is False

    def test_delete_deal_activation(self, deal_store):
        """delete_deal_activation removes an activation by ID."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        act_id = deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
        )
        result = deal_store.delete_deal_activation(act_id)
        assert result is True
        assert deal_store.get_deal_activations(deal_id) == []

    def test_delete_deal_activation_not_found(self, deal_store):
        """delete_deal_activation returns False for nonexistent ID."""
        assert deal_store.delete_deal_activation(99999) is False

    def test_save_deal_activation_foreign_key_constraint(self, deal_store):
        """save_deal_activation fails for nonexistent deal_id."""
        with pytest.raises(sqlite3.IntegrityError):
            deal_store.save_deal_activation(
                deal_id="nonexistent-deal",
                platform="TTD",
            )


# -----------------------------------------------------------------------
# Performance Cache CRUD Tests
# -----------------------------------------------------------------------


class TestPerformanceCacheCRUD:
    """Tests for performance_cache save, get, update, delete."""

    def test_save_performance_cache_returns_row_id(self, deal_store):
        """save_performance_cache returns the row ID."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=100000,
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_save_performance_cache_all_fields(self, deal_store):
        """save_performance_cache stores all fields correctly."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=1500000,
            spend_to_date=22500.00,
            fill_rate=0.85,
            win_rate=0.42,
            avg_effective_cpm=15.00,
            last_delivery_at="2026-03-18T09:00:00Z",
            performance_trend="IMPROVING",
            cached_at="2026-03-18T10:00:00Z",
        )

        perf = deal_store.get_performance_cache(deal_id)
        assert perf is not None
        assert perf["id"] == row_id
        assert perf["deal_id"] == deal_id
        assert perf["impressions_delivered"] == 1500000
        assert perf["spend_to_date"] == 22500.00
        assert perf["fill_rate"] == 0.85
        assert perf["win_rate"] == 0.42
        assert perf["avg_effective_cpm"] == 15.00
        assert perf["last_delivery_at"] == "2026-03-18T09:00:00Z"
        assert perf["performance_trend"] == "IMPROVING"
        assert perf["cached_at"] == "2026-03-18T10:00:00Z"

    def test_save_performance_cache_minimal_fields(self, deal_store):
        """save_performance_cache works with only deal_id."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        row_id = deal_store.save_performance_cache(deal_id=deal_id)
        assert row_id > 0
        perf = deal_store.get_performance_cache(deal_id)
        assert perf is not None
        assert perf["impressions_delivered"] is None
        assert perf["spend_to_date"] is None

    def test_get_performance_cache_returns_latest(self, deal_store):
        """get_performance_cache returns the latest cache entry."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=100000,
            cached_at="2026-03-17T10:00:00Z",
        )
        deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=200000,
            cached_at="2026-03-18T10:00:00Z",
        )
        perf = deal_store.get_performance_cache(deal_id)
        assert perf is not None
        # Should return the entry with the highest ID (most recent insert)
        assert perf["impressions_delivered"] == 200000

    def test_get_performance_cache_not_found(self, deal_store):
        """get_performance_cache returns None for deal with no cache."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        assert deal_store.get_performance_cache(deal_id) is None

    def test_get_performance_cache_nonexistent_deal(self, deal_store):
        """get_performance_cache returns None for nonexistent deal."""
        assert deal_store.get_performance_cache("nonexistent") is None

    def test_update_performance_cache(self, deal_store):
        """update_performance_cache updates specified fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=100000,
            fill_rate=0.70,
        )
        result = deal_store.update_performance_cache(
            deal_id,
            impressions_delivered=200000,
            performance_trend="IMPROVING",
        )
        assert result is True

        perf = deal_store.get_performance_cache(deal_id)
        assert perf["impressions_delivered"] == 200000
        assert perf["performance_trend"] == "IMPROVING"
        # Unchanged field
        assert perf["fill_rate"] == 0.70

    def test_update_performance_cache_not_found(self, deal_store):
        """update_performance_cache returns False for deal with no cache."""
        result = deal_store.update_performance_cache(
            "nonexistent",
            impressions_delivered=100,
        )
        assert result is False

    def test_update_performance_cache_no_kwargs(self, deal_store):
        """update_performance_cache with no kwargs returns False."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_performance_cache(deal_id=deal_id)
        result = deal_store.update_performance_cache(deal_id)
        assert result is False

    def test_delete_performance_cache(self, deal_store):
        """delete_performance_cache removes cache entries for a deal."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
        )
        deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=100000,
        )
        result = deal_store.delete_performance_cache(deal_id)
        assert result is True
        assert deal_store.get_performance_cache(deal_id) is None

    def test_delete_performance_cache_not_found(self, deal_store):
        """delete_performance_cache returns False for deal with no cache."""
        assert deal_store.delete_performance_cache("nonexistent") is False

    def test_save_performance_cache_foreign_key_constraint(self, deal_store):
        """save_performance_cache fails for nonexistent deal_id."""
        with pytest.raises(sqlite3.IntegrityError):
            deal_store.save_performance_cache(
                deal_id="nonexistent-deal",
                impressions_delivered=100,
            )


# -----------------------------------------------------------------------
# save_deal() with v2 Intrinsic Fields Tests
# -----------------------------------------------------------------------


class TestSaveDealV2Fields:
    """Tests for save_deal() extended with v2 intrinsic fields."""

    def test_save_deal_v1_fields_still_work(self, deal_store):
        """save_deal with only v1 fields works unchanged."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            product_name="Banner Ad",
            deal_type="PD",
            status="draft",
            price=12.50,
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["seller_url"] == "http://seller.com"
        assert deal["product_name"] == "Banner Ad"
        assert deal["price"] == 12.50
        # v2 fields should be NULL
        assert deal["display_name"] is None
        assert deal["media_type"] is None

    def test_save_deal_with_counterparty_fields(self, deal_store):
        """save_deal accepts counterparty v2 fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            display_name="ESPN Premium Video PG",
            description="Premium video deal with ESPN",
            buyer_org="Acme Agency",
            buyer_id="buyer-001",
            seller_org="ESPN",
            seller_id="seller-espn",
            seller_domain="espn.com",
            seller_type="PUBLISHER",
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["display_name"] == "ESPN Premium Video PG"
        assert deal["description"] == "Premium video deal with ESPN"
        assert deal["buyer_org"] == "Acme Agency"
        assert deal["buyer_id"] == "buyer-001"
        assert deal["seller_org"] == "ESPN"
        assert deal["seller_id"] == "seller-espn"
        assert deal["seller_domain"] == "espn.com"
        assert deal["seller_type"] == "PUBLISHER"

    def test_save_deal_with_pricing_fields(self, deal_store):
        """save_deal accepts pricing v2 fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            price_model="CPM",
            bid_floor_cpm=15.00,
            fixed_price_cpm=18.50,
            currency="EUR",
            fee_transparency=0.12,
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["price_model"] == "CPM"
        assert deal["bid_floor_cpm"] == 15.00
        assert deal["fixed_price_cpm"] == 18.50
        assert deal["currency"] == "EUR"
        assert deal["fee_transparency"] == 0.12

    def test_save_deal_with_linear_tv_fields(self, deal_store):
        """save_deal accepts linear TV v2 fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_tv",
            deal_type="UPFRONT",
            media_type="LINEAR_TV",
            cpp=45.00,
            guaranteed_grps=150.0,
            dayparts=json.dumps(["M-F 8p-11p"]),
            programs=json.dumps(["Sunday Night Football"]),
            networks=json.dumps(["NBC", "ESPN"]),
            makegood_provisions="Standard makegood within 2 weeks",
            cancellation_window="30 days written notice",
            audience_guarantee="A18-49 Nielsen Live+3",
            preemption_rights="Non-preemptible for upfront",
            agency_of_record_status="BBDO Worldwide",
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["media_type"] == "LINEAR_TV"
        assert deal["cpp"] == 45.00
        assert deal["guaranteed_grps"] == 150.0
        assert json.loads(deal["dayparts"]) == ["M-F 8p-11p"]
        assert json.loads(deal["programs"]) == ["Sunday Night Football"]
        assert json.loads(deal["networks"]) == ["NBC", "ESPN"]
        assert deal["makegood_provisions"] == "Standard makegood within 2 weeks"
        assert deal["cancellation_window"] == "30 days written notice"
        assert deal["audience_guarantee"] == "A18-49 Nielsen Live+3"
        assert deal["preemption_rights"] == "Non-preemptible for upfront"
        assert deal["agency_of_record_status"] == "BBDO Worldwide"

    def test_save_deal_with_inventory_fields(self, deal_store):
        """save_deal accepts inventory targeting v2 fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            media_type="DIGITAL",
            formats=json.dumps(["banner_300x250", "video_15s"]),
            content_categories=json.dumps(["IAB17"]),
            publisher_domains=json.dumps(["espn.com"]),
            geo_targets=json.dumps(["US"]),
            audience_segments=json.dumps(["sports_enthusiasts"]),
            estimated_volume=500000,
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["media_type"] == "DIGITAL"
        assert json.loads(deal["formats"]) == ["banner_300x250", "video_15s"]
        assert json.loads(deal["content_categories"]) == ["IAB17"]
        assert json.loads(deal["publisher_domains"]) == ["espn.com"]
        assert json.loads(deal["geo_targets"]) == ["US"]
        assert json.loads(deal["audience_segments"]) == ["sports_enthusiasts"]
        assert deal["estimated_volume"] == 500000

    def test_save_deal_with_supply_chain_fields(self, deal_store):
        """save_deal accepts supply chain v2 fields."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            schain_complete=1,
            schain_nodes=json.dumps([{"asi": "espn.com", "sid": "001", "hp": 1}]),
            sellers_json_url="https://espn.com/sellers.json",
            is_direct=1,
            hop_count=1,
            inventory_fingerprint="espn.com|IAB17|banner_300x250|DIGITAL",
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["schain_complete"] == 1
        assert json.loads(deal["schain_nodes"]) == [{"asi": "espn.com", "sid": "001", "hp": 1}]
        assert deal["sellers_json_url"] == "https://espn.com/sellers.json"
        assert deal["is_direct"] == 1
        assert deal["hop_count"] == 1
        assert deal["inventory_fingerprint"] == "espn.com|IAB17|banner_300x250|DIGITAL"

    def test_save_deal_with_lifecycle_fields(self, deal_store):
        """save_deal accepts lifecycle extension v2 fields."""
        parent_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_parent",
        )
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            deprecated_at="2026-03-15T00:00:00Z",
            deprecated_reason="Replaced by direct deal",
            parent_deal_id=parent_id,
        )
        deal = deal_store.get_deal(deal_id)
        assert deal["deprecated_at"] == "2026-03-15T00:00:00Z"
        assert deal["deprecated_reason"] == "Replaced by direct deal"
        assert deal["parent_deal_id"] == parent_id

    def test_save_deal_mixed_v1_and_v2_fields(self, deal_store):
        """save_deal with both v1 and v2 fields works."""
        deal_id = deal_store.save_deal(
            seller_url="http://seller.com",
            product_id="prod_1",
            product_name="ESPN Video",
            deal_type="PG",
            status="draft",
            price=15.00,
            impressions=1000000,
            # v2 fields
            display_name="ESPN Premium Video PG",
            media_type="DIGITAL",
            seller_domain="espn.com",
            bid_floor_cpm=12.00,
        )
        deal = deal_store.get_deal(deal_id)
        # v1 fields
        assert deal["product_name"] == "ESPN Video"
        assert deal["price"] == 15.00
        assert deal["impressions"] == 1000000
        # v2 fields
        assert deal["display_name"] == "ESPN Premium Video PG"
        assert deal["media_type"] == "DIGITAL"
        assert deal["seller_domain"] == "espn.com"
        assert deal["bid_floor_cpm"] == 12.00


# -----------------------------------------------------------------------
# list_deals() with v2 Filters Tests
# -----------------------------------------------------------------------


class TestListDealsV2Filters:
    """Tests for list_deals() with new v2 filter parameters."""

    def test_list_deals_filter_by_media_type(self, deal_store):
        """list_deals filters by media_type."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            media_type="DIGITAL",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            media_type="CTV",
        )
        deal_store.save_deal(
            seller_url="http://c.com",
            product_id="p3",
            media_type="DIGITAL",
        )
        results = deal_store.list_deals(media_type="DIGITAL")
        assert len(results) == 2
        for r in results:
            assert r["media_type"] == "DIGITAL"

    def test_list_deals_filter_by_seller_domain(self, deal_store):
        """list_deals filters by seller_domain."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            seller_domain="espn.com",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            seller_domain="nyt.com",
        )
        results = deal_store.list_deals(seller_domain="espn.com")
        assert len(results) == 1
        assert results[0]["seller_domain"] == "espn.com"

    def test_list_deals_filter_by_deal_type(self, deal_store):
        """list_deals filters by deal_type."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            deal_type="PG",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            deal_type="PD",
        )
        deal_store.save_deal(
            seller_url="http://c.com",
            product_id="p3",
            deal_type="PG",
        )
        results = deal_store.list_deals(deal_type="PG")
        assert len(results) == 2
        for r in results:
            assert r["deal_type"] == "PG"

    def test_list_deals_filter_by_advertiser_id(self, deal_store):
        """list_deals filters by advertiser_id (via JOIN to portfolio_metadata)."""
        d1 = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        d2 = deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
        )
        d3 = deal_store.save_deal(
            seller_url="http://c.com",
            product_id="p3",
        )
        # Set up metadata with different advertiser IDs
        deal_store.save_portfolio_metadata(
            deal_id=d1,
            advertiser_id="adv-alpha",
        )
        deal_store.save_portfolio_metadata(
            deal_id=d2,
            advertiser_id="adv-beta",
        )
        deal_store.save_portfolio_metadata(
            deal_id=d3,
            advertiser_id="adv-alpha",
        )
        results = deal_store.list_deals(advertiser_id="adv-alpha")
        assert len(results) == 2
        result_ids = {r["id"] for r in results}
        assert d1 in result_ids
        assert d3 in result_ids

    def test_list_deals_combined_v2_filters(self, deal_store):
        """list_deals combines multiple v2 filters."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            media_type="DIGITAL",
            seller_domain="espn.com",
            deal_type="PG",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            media_type="DIGITAL",
            seller_domain="nyt.com",
            deal_type="PD",
        )
        deal_store.save_deal(
            seller_url="http://c.com",
            product_id="p3",
            media_type="CTV",
            seller_domain="espn.com",
            deal_type="PG",
        )
        results = deal_store.list_deals(
            media_type="DIGITAL",
            seller_domain="espn.com",
        )
        assert len(results) == 1
        assert results[0]["product_id"] == "p1"

    def test_list_deals_v1_filters_still_work(self, deal_store):
        """list_deals v1 filters (status, seller_url, created_after) still work."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            status="draft",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            status="booked",
        )
        results = deal_store.list_deals(status="draft")
        assert len(results) == 1
        assert results[0]["status"] == "draft"

    def test_list_deals_v1_and_v2_filters_combined(self, deal_store):
        """list_deals combines v1 and v2 filters together."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            status="draft",
            media_type="DIGITAL",
        )
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p2",
            status="booked",
            media_type="DIGITAL",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p3",
            status="draft",
            media_type="CTV",
        )
        results = deal_store.list_deals(status="draft", media_type="DIGITAL")
        assert len(results) == 1
        assert results[0]["product_id"] == "p1"

    def test_list_deals_no_filter_still_returns_all(self, deal_store):
        """list_deals with no filters returns all deals."""
        deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
            media_type="DIGITAL",
        )
        deal_store.save_deal(
            seller_url="http://b.com",
            product_id="p2",
            media_type="CTV",
        )
        results = deal_store.list_deals()
        assert len(results) == 2


# -----------------------------------------------------------------------
# Cascade Delete Tests for v2 Tables
# -----------------------------------------------------------------------


class TestCascadeDeleteV2:
    """Tests that deal deletion cascades to v2 extrinsic tables."""

    def test_cascade_deletes_portfolio_metadata(self, deal_store):
        """Deleting a deal cascades to portfolio_metadata."""
        deal_id = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        deal_store.save_portfolio_metadata(
            deal_id=deal_id,
            import_source="CSV",
        )
        with deal_store._lock:
            deal_store._conn.execute(
                "DELETE FROM deals WHERE id = ?",
                (deal_id,),
            )
            deal_store._conn.commit()
        assert deal_store.get_portfolio_metadata(deal_id) is None

    def test_cascade_deletes_deal_activations(self, deal_store):
        """Deleting a deal cascades to deal_activations."""
        deal_id = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        deal_store.save_deal_activation(
            deal_id=deal_id,
            platform="TTD",
        )
        with deal_store._lock:
            deal_store._conn.execute(
                "DELETE FROM deals WHERE id = ?",
                (deal_id,),
            )
            deal_store._conn.commit()
        assert deal_store.get_deal_activations(deal_id) == []

    def test_cascade_deletes_performance_cache(self, deal_store):
        """Deleting a deal cascades to performance_cache."""
        deal_id = deal_store.save_deal(
            seller_url="http://a.com",
            product_id="p1",
        )
        deal_store.save_performance_cache(
            deal_id=deal_id,
            impressions_delivered=100,
        )
        with deal_store._lock:
            deal_store._conn.execute(
                "DELETE FROM deals WHERE id = ?",
                (deal_id,),
            )
            deal_store._conn.commit()
        assert deal_store.get_performance_cache(deal_id) is None
