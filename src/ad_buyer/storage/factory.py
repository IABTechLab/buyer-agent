# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Storage backend factory.

Mirrors the seller-agent's factory pattern for structural consistency.
Supports SQLite (default), Redis, and Hybrid backends.
"""


from ad_buyer.storage.base import StorageBackend
from ad_buyer.storage.sqlite_backend import SQLiteBackend


def get_storage_backend(
    storage_type: str | None = None,
    database_url: str | None = None,
    redis_url: str | None = None,
) -> StorageBackend:
    """Create and return the appropriate storage backend.

    Args:
        storage_type: Type of storage ("sqlite", "redis", or "hybrid").
                      If None, auto-detects from available config.
        database_url: Database URL (for sqlite or hybrid backends)
        redis_url: Redis connection URL (for redis or hybrid backends)

    Returns:
        StorageBackend instance

    Raises:
        ValueError: If invalid storage type or missing configuration
    """
    from ad_buyer.config.settings import settings

    storage_type = storage_type or getattr(settings, "storage_type", "sqlite")
    database_url = database_url or settings.database_url
    redis_url = redis_url or getattr(settings, "redis_url", None)

    if storage_type is None:
        storage_type = "redis" if redis_url else "sqlite"

    storage_type = storage_type.lower()

    if storage_type == "sqlite":
        if not database_url:
            database_url = "sqlite:///./ad_buyer.db"
        return SQLiteBackend(database_url=database_url)

    elif storage_type == "redis":
        if not redis_url:
            raise ValueError(
                "Redis URL required for redis storage. "
                "Set REDIS_URL environment variable or pass redis_url parameter."
            )
        # Lazy import to avoid requiring redis dependency
        from ad_buyer.storage.redis_backend import RedisBackend

        return RedisBackend(redis_url=redis_url)

    elif storage_type == "hybrid":
        if not database_url or not database_url.startswith("postgresql"):
            raise ValueError(
                "PostgreSQL URL required for hybrid storage. "
                "Set DATABASE_URL=postgresql+asyncpg://user:pass@host/db"
            )
        if not redis_url:
            raise ValueError(
                "Redis URL required for hybrid storage. Set REDIS_URL=redis://host:6379/0"
            )
        from ad_buyer.storage.hybrid_backend import HybridBackend
        from ad_buyer.storage.postgres_backend import PostgresBackend
        from ad_buyer.storage.redis_backend import RedisBackend

        pg = PostgresBackend(
            database_url=database_url,
            pool_min=getattr(settings, "postgres_pool_min", 2),
            pool_max=getattr(settings, "postgres_pool_max", 10),
        )
        redis = RedisBackend(redis_url=redis_url)
        return HybridBackend(postgres=pg, redis=redis)

    else:
        raise ValueError(
            f"Unknown storage type: {storage_type}. Supported types: sqlite, redis, hybrid"
        )


# Global storage instance (lazy initialization)
_storage_instance: StorageBackend | None = None


async def get_storage() -> StorageBackend:
    """Get the global storage instance, creating it if needed."""
    global _storage_instance

    if _storage_instance is None:
        _storage_instance = get_storage_backend()
        await _storage_instance.connect()

    return _storage_instance


async def close_storage() -> None:
    """Close the global storage instance."""
    global _storage_instance

    if _storage_instance is not None:
        await _storage_instance.disconnect()
        _storage_instance = None
