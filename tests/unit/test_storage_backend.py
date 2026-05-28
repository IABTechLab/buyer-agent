# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the StorageBackend ABC, SQLiteBackend, and factory."""

import asyncio
import os
import tempfile

import pytest

from ad_buyer.storage.base import StorageBackend
from ad_buyer.storage.factory import get_storage_backend
from ad_buyer.storage.sqlite_backend import SQLiteBackend


@pytest.fixture
def tmp_db_path():
    """Create a temporary database file path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def db_url(tmp_db_path):
    """Create a SQLite URL from temp path."""
    return f"sqlite:///{tmp_db_path}"


class TestSQLiteBackend:
    """Test the SQLiteBackend implementation."""

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        assert backend._connection is not None
        await backend.disconnect()
        assert backend._connection is None

    @pytest.mark.asyncio
    async def test_set_and_get(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            await backend.set("test:key", {"hello": "world"})
            result = await backend.get("test:key")
            assert result == {"hello": "world"}
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            result = await backend.get("nonexistent")
            assert result is None
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_delete(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            await backend.set("test:del", "value")
            assert await backend.delete("test:del") is True
            assert await backend.get("test:del") is None
            assert await backend.delete("test:del") is False
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_exists(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            assert await backend.exists("test:exists") is False
            await backend.set("test:exists", "val")
            assert await backend.exists("test:exists") is True
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_keys_pattern(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            await backend.set("deal:1", {"id": 1})
            await backend.set("deal:2", {"id": 2})
            await backend.set("campaign:1", {"id": 1})

            deal_keys = await backend.keys("deal:*")
            assert len(deal_keys) == 2
            assert "deal:1" in deal_keys
            assert "deal:2" in deal_keys

            campaign_keys = await backend.keys("campaign:*")
            assert len(campaign_keys) == 1
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            # Set with 1-second TTL and wait for expiration
            await backend.set("test:ttl", "expired", ttl=1)
            # Verify it exists immediately
            result = await backend.get("test:ttl")
            assert result == "expired"
            # Wait for expiration
            await asyncio.sleep(1.1)
            result = await backend.get("test:ttl")
            assert result is None
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_upsert(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            await backend.set("test:up", {"v": 1})
            await backend.set("test:up", {"v": 2})
            result = await backend.get("test:up")
            assert result == {"v": 2}
        finally:
            await backend.disconnect()


class TestStorageBackendDomainHelpers:
    """Test the domain helper methods on StorageBackend."""

    @pytest.mark.asyncio
    async def test_deal_helpers(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            deal = {"deal_id": "DEAL-001", "status": "active", "deal_type": "PG"}
            await backend.set_deal("DEAL-001", deal)
            result = await backend.get_deal("DEAL-001")
            assert result["deal_id"] == "DEAL-001"

            deals = await backend.list_deals()
            assert len(deals) == 1

            deals_filtered = await backend.list_deals({"status": "inactive"})
            assert len(deals_filtered) == 0
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_campaign_helpers(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            campaign = {"campaign_id": "C-001", "status": "active"}
            await backend.set_campaign("C-001", campaign)
            result = await backend.get_campaign("C-001")
            assert result["campaign_id"] == "C-001"
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_conversion_helpers(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            conversion = {"event_id": "E-001", "deal_id": "D-001", "campaign_id": "C-001"}
            await backend.set_conversion("E-001", conversion)
            result = await backend.get_conversion("E-001")
            assert result["event_id"] == "E-001"

            conversions = await backend.list_conversions({"deal_id": "D-001"})
            assert len(conversions) == 1

            conversions_miss = await backend.list_conversions({"deal_id": "D-999"})
            assert len(conversions_miss) == 0
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_experiment_helpers(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            exp = {"experiment_id": "EXP-001", "campaign_id": "C-001", "status": "active"}
            await backend.set_experiment("EXP-001", exp)
            result = await backend.get_experiment("EXP-001")
            assert result["experiment_id"] == "EXP-001"

            exps = await backend.list_experiments({"campaign_id": "C-001"})
            assert len(exps) == 1
        finally:
            await backend.disconnect()

    @pytest.mark.asyncio
    async def test_supply_path_helpers(self, db_url):
        backend = SQLiteBackend(db_url)
        await backend.connect()
        try:
            score = {"supply_path_hash": "abc123", "composite_score": 0.85}
            await backend.set_supply_path_score("abc123", score)
            result = await backend.get_supply_path_score("abc123")
            assert result["composite_score"] == 0.85

            scores = await backend.list_supply_path_scores()
            assert len(scores) == 1
        finally:
            await backend.disconnect()


class TestStorageFactory:
    """Test the storage backend factory."""

    def test_factory_returns_sqlite_by_default(self):
        backend = get_storage_backend(
            storage_type="sqlite",
            database_url="sqlite:///./test_factory.db",
        )
        assert isinstance(backend, SQLiteBackend)

    def test_factory_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown storage type"):
            get_storage_backend(storage_type="invalid")

    def test_factory_redis_requires_url(self, monkeypatch):
        # Ensure ambient REDIS_URL (e.g. set by CI) does not satisfy the check.
        from ad_buyer.config.settings import settings

        monkeypatch.setattr(settings, "redis_url", None, raising=False)
        with pytest.raises(ValueError, match="Redis URL required"):
            get_storage_backend(storage_type="redis", redis_url=None)

    def test_factory_hybrid_requires_postgres_url(self):
        with pytest.raises(ValueError, match="PostgreSQL URL required"):
            get_storage_backend(
                storage_type="hybrid",
                database_url="sqlite:///./test.db",
                redis_url="redis://localhost:6379/0",
            )

    def test_factory_hybrid_requires_redis_url(self, monkeypatch):
        # Ensure ambient REDIS_URL (e.g. set by CI) does not satisfy the check.
        from ad_buyer.config.settings import settings

        monkeypatch.setattr(settings, "redis_url", None, raising=False)
        with pytest.raises(ValueError, match="Redis URL required"):
            get_storage_backend(
                storage_type="hybrid",
                database_url="postgresql+asyncpg://user:pass@localhost/db",
                redis_url=None,
            )

    def test_sqlite_backend_is_storage_backend(self):
        """Verify SQLiteBackend implements the StorageBackend ABC."""
        backend = SQLiteBackend("sqlite:///./test.db")
        assert isinstance(backend, StorageBackend)


class TestPostgresBackendInit:
    """Test PostgresBackend can be instantiated (no live connection)."""

    def test_postgres_importable(self):
        from ad_buyer.storage.postgres_backend import PostgresBackend

        backend = PostgresBackend(
            database_url="postgresql+asyncpg://user:pass@localhost/test",
            pool_min=1,
            pool_max=5,
        )
        assert backend._dsn == "postgresql://user:pass@localhost/test"
        assert backend._pool_min == 1
        assert backend._pool_max == 5
        assert isinstance(backend, StorageBackend)

    def test_postgres_url_normalization(self):
        from ad_buyer.storage.postgres_backend import PostgresBackend

        backend = PostgresBackend(database_url="postgresql://user:pass@host/db")
        assert backend._dsn == "postgresql://user:pass@host/db"

    @pytest.mark.asyncio
    async def test_postgres_not_connected_raises(self):
        from ad_buyer.storage.postgres_backend import PostgresBackend

        backend = PostgresBackend(database_url="postgresql://localhost/test")
        with pytest.raises(RuntimeError, match="not connected"):
            await backend.get("test")


class TestHybridBackendRouting:
    """Test HybridBackend routes keys correctly."""

    def test_hybrid_importable(self):
        from ad_buyer.storage.hybrid_backend import HybridBackend

        assert HybridBackend is not None

    def test_redis_key_detection(self):
        from ad_buyer.storage.hybrid_backend import _is_redis_key

        # Should route to Redis
        assert _is_redis_key("session:abc") is True
        assert _is_redis_key("session_index:buyer:123") is True
        assert _is_redis_key("cache:products") is True
        assert _is_redis_key("lock:deal:123") is True
        assert _is_redis_key("rate_limit:api") is True

        # Should route to Postgres
        assert _is_redis_key("deal:DEAL-001") is False
        assert _is_redis_key("campaign:C-001") is False
        assert _is_redis_key("order:O-001") is False
        assert _is_redis_key("conversion:E-001") is False
        assert _is_redis_key("opt_decision:D-001") is False
        assert _is_redis_key("experiment:EXP-001") is False
        assert _is_redis_key("supply_path:abc") is False
        assert _is_redis_key("model:lightgbm") is False

    @pytest.mark.asyncio
    async def test_hybrid_routes_to_correct_backend(self, db_url):
        """Hybrid backend routes durable keys to PG and ephemeral to Redis.

        Since we can't spin up real PG/Redis in unit tests, we use two
        SQLiteBackends to verify the routing logic.
        """
        from ad_buyer.storage.hybrid_backend import HybridBackend

        # Use two separate SQLite backends as stand-ins
        import tempfile

        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)

        pg_backend = SQLiteBackend(db_url)
        redis_backend = SQLiteBackend(f"sqlite:///{path2}")

        hybrid = HybridBackend(postgres=pg_backend, redis=redis_backend)
        await hybrid.connect()

        try:
            # Store a deal (should go to pg) and a session (should go to redis)
            await hybrid.set("deal:D-001", {"id": "D-001"})
            await hybrid.set("session:S-001", {"id": "S-001"})

            # Verify deal is in pg, not redis
            assert await pg_backend.get("deal:D-001") == {"id": "D-001"}
            assert await redis_backend.get("deal:D-001") is None

            # Verify session is in redis, not pg
            assert await redis_backend.get("session:S-001") == {"id": "S-001"}
            assert await pg_backend.get("session:S-001") is None

            # Verify hybrid can read both
            assert await hybrid.get("deal:D-001") == {"id": "D-001"}
            assert await hybrid.get("session:S-001") == {"id": "S-001"}

            # Verify keys merges from both
            all_keys = await hybrid.keys("*")
            assert "deal:D-001" in all_keys
            assert "session:S-001" in all_keys
        finally:
            await hybrid.disconnect()
            if os.path.exists(path2):
                os.unlink(path2)


class TestRedisBackendInit:
    """Test RedisBackend initialization (no live Redis connection)."""

    def test_redis_import_check(self):
        """Verify redis_backend module is importable."""
        from ad_buyer.storage.redis_backend import REDIS_AVAILABLE

        # REDIS_AVAILABLE depends on whether redis is installed
        assert isinstance(REDIS_AVAILABLE, bool)

    def test_redis_is_storage_backend(self):
        from ad_buyer.storage.redis_backend import REDIS_AVAILABLE

        if not REDIS_AVAILABLE:
            pytest.skip("redis package not installed")

        from ad_buyer.storage.redis_backend import RedisBackend

        backend = RedisBackend(redis_url="redis://localhost:6379/0")
        assert isinstance(backend, StorageBackend)
        assert backend.key_prefix == "ad_buyer:"
