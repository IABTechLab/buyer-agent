# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""SQLite storage backend implementation.

Mirrors the seller-agent's SQLiteBackend for structural consistency.
Uses a simple key-value store pattern with JSON serialization.
Suitable for development and single-instance deployments.
"""

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from ad_buyer.storage.base import StorageBackend


class SQLiteBackend(StorageBackend):
    """SQLite-based storage backend.

    Uses a simple key-value store pattern with JSON serialization.
    Suitable for development and single-instance deployments.
    """

    def __init__(self, database_url: str):
        """Initialize SQLite backend.

        Args:
            database_url: SQLite connection string (e.g., sqlite:///./ad_buyer.db)
        """
        if database_url.startswith("sqlite:///"):
            self.db_path = database_url[len("sqlite:///") :]
        elif database_url.startswith("sqlite://"):
            self.db_path = database_url[len("sqlite://") :]
        else:
            self.db_path = database_url

        self._connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Establish connection and create tables."""
        db_dir = Path(self.db_path).parent
        if db_dir and str(db_dir) != "." and not db_dir.exists():
            db_dir.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)

        await self._connection.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at REAL
            )
        """)

        await self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_expires_at ON kv_store(expires_at)
        """)

        await self._connection.commit()

    async def disconnect(self) -> None:
        """Close the database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None

    async def _cleanup_expired(self) -> None:
        """Remove expired entries."""
        if self._connection:
            current_time = time.time()
            await self._connection.execute(
                "DELETE FROM kv_store WHERE expires_at IS NOT NULL AND expires_at < ?",
                (current_time,),
            )
            await self._connection.commit()

    async def get(self, key: str) -> Any | None:
        """Retrieve a value by key."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        await self._cleanup_expired()

        async with self._connection.execute(
            "SELECT value, expires_at FROM kv_store WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()

            if row is None:
                return None

            value, expires_at = row

            if expires_at is not None and expires_at < time.time():
                await self.delete(key)
                return None

            return json.loads(value)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value with optional TTL (seconds)."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        expires_at = time.time() + ttl if ttl else None
        json_value = json.dumps(value)

        await self._connection.execute(
            """
            INSERT OR REPLACE INTO kv_store (key, value, expires_at)
            VALUES (?, ?, ?)
            """,
            (key, json_value, expires_at),
        )
        await self._connection.commit()

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        async with self._connection.execute("DELETE FROM kv_store WHERE key = ?", (key,)) as cursor:
            await self._connection.commit()
            return cursor.rowcount > 0

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        await self._cleanup_expired()

        async with self._connection.execute(
            "SELECT 1 FROM kv_store WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (key, time.time()),
        ) as cursor:
            row = await cursor.fetchone()
            return row is not None

    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching pattern.

        Supports basic wildcards:
        - * matches any characters
        - ? matches single character
        """
        if not self._connection:
            raise RuntimeError("Storage not connected. Call connect() first.")

        await self._cleanup_expired()

        sql_pattern = pattern.replace("*", "%").replace("?", "_")

        async with self._connection.execute(
            "SELECT key FROM kv_store WHERE key LIKE ? AND (expires_at IS NULL OR expires_at > ?)",
            (sql_pattern, time.time()),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
