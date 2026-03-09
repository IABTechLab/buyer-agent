# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for session persistence client."""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_buyer.sessions.session_store import SessionRecord, SessionStore
from ad_buyer.sessions.session_manager import SessionManager


class TestSessionRecord:
    """Tests for SessionRecord dataclass."""

    def test_create_record(self):
        """Test creating a session record."""
        record = SessionRecord(
            session_id="sess-123",
            seller_url="http://seller1.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        assert record.session_id == "sess-123"
        assert record.seller_url == "http://seller1.example.com"

    def test_is_expired_false(self):
        """Test that a fresh session is not expired."""
        record = SessionRecord(
            session_id="sess-123",
            seller_url="http://seller1.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        assert record.is_expired() is False

    def test_is_expired_true(self):
        """Test that an old session is expired."""
        record = SessionRecord(
            session_id="sess-123",
            seller_url="http://seller1.example.com",
            created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        assert record.is_expired() is True

    def test_to_dict_and_from_dict(self):
        """Test round-trip serialization."""
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        record = SessionRecord(
            session_id="sess-456",
            seller_url="http://seller2.example.com",
            created_at=now,
            expires_at=expires,
        )
        d = record.to_dict()
        restored = SessionRecord.from_dict(d)
        assert restored.session_id == record.session_id
        assert restored.seller_url == record.seller_url
        assert restored.created_at == record.created_at
        assert restored.expires_at == record.expires_at


class TestSessionStore:
    """Tests for file-backed SessionStore."""

    @pytest.fixture
    def store_path(self, tmp_path):
        """Create a temporary store file path."""
        return str(tmp_path / "sessions.json")

    @pytest.fixture
    def store(self, store_path):
        """Create a SessionStore with a temporary file."""
        return SessionStore(store_path)

    def test_store_empty_initially(self, store):
        """Test that a new store has no sessions."""
        assert store.list_sessions() == {}

    def test_save_and_get_session(self, store):
        """Test saving and retrieving a session."""
        record = SessionRecord(
            session_id="sess-abc",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(record)
        retrieved = store.get("http://seller.example.com")
        assert retrieved is not None
        assert retrieved.session_id == "sess-abc"

    def test_get_nonexistent_returns_none(self, store):
        """Test that getting a nonexistent session returns None."""
        assert store.get("http://no-such-seller.example.com") is None

    def test_remove_session(self, store):
        """Test removing a session."""
        record = SessionRecord(
            session_id="sess-del",
            seller_url="http://seller-del.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store.save(record)
        store.remove("http://seller-del.example.com")
        assert store.get("http://seller-del.example.com") is None

    def test_remove_nonexistent_is_noop(self, store):
        """Test that removing a nonexistent session doesn't raise."""
        store.remove("http://no-such.example.com")  # Should not raise

    def test_list_sessions(self, store):
        """Test listing all sessions."""
        for i in range(3):
            record = SessionRecord(
                session_id=f"sess-{i}",
                seller_url=f"http://seller{i}.example.com",
                created_at=datetime.now(timezone.utc).isoformat(),
                expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            )
            store.save(record)
        sessions = store.list_sessions()
        assert len(sessions) == 3

    def test_persistence_across_restarts(self, store_path):
        """Test that sessions persist across store instances."""
        store1 = SessionStore(store_path)
        record = SessionRecord(
            session_id="sess-persist",
            seller_url="http://persistent.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        store1.save(record)

        # Create a new store instance pointing to the same file
        store2 = SessionStore(store_path)
        retrieved = store2.get("http://persistent.example.com")
        assert retrieved is not None
        assert retrieved.session_id == "sess-persist"

    def test_expired_sessions_filtered_on_get(self, store):
        """Test that expired sessions are filtered out on get."""
        record = SessionRecord(
            session_id="sess-expired",
            seller_url="http://expired.example.com",
            created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        store.save(record)
        assert store.get("http://expired.example.com") is None

    def test_cleanup_expired(self, store):
        """Test cleanup removes expired sessions from the store file."""
        # Add one valid and one expired
        valid = SessionRecord(
            session_id="sess-valid",
            seller_url="http://valid.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        expired = SessionRecord(
            session_id="sess-expired",
            seller_url="http://expired.example.com",
            created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        store.save(valid)
        store.save(expired)
        removed = store.cleanup_expired()
        assert removed == 1
        assert store.get("http://valid.example.com") is not None
        assert len(store.list_sessions()) == 1


class TestSessionManager:
    """Tests for SessionManager."""

    @pytest.fixture
    def store_path(self, tmp_path):
        return str(tmp_path / "sessions.json")

    @pytest.fixture
    def manager(self, store_path):
        return SessionManager(store_path=store_path)

    @pytest.mark.asyncio
    async def test_create_session(self, manager):
        """Test creating a new session with a seller."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "sess-new-123",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            session_id = await manager.create_session(
                seller_url="http://seller.example.com",
                buyer_identity={"seat_id": "seat-abc", "name": "Test Buyer"},
            )

        assert session_id == "sess-new-123"
        # Session should be stored
        stored = manager.store.get("http://seller.example.com")
        assert stored is not None
        assert stored.session_id == "sess-new-123"

    @pytest.mark.asyncio
    async def test_get_or_create_session_reuses_existing(self, manager):
        """Test that get_or_create returns an existing active session."""
        # Pre-populate store with an active session
        record = SessionRecord(
            session_id="sess-existing",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        # Should return existing without making HTTP call
        session_id = await manager.get_or_create_session(
            seller_url="http://seller.example.com",
            buyer_identity={"seat_id": "seat-abc"},
        )
        assert session_id == "sess-existing"

    @pytest.mark.asyncio
    async def test_get_or_create_session_creates_when_expired(self, manager):
        """Test that get_or_create creates a new session when existing one is expired."""
        # Pre-populate store with an expired session
        record = SessionRecord(
            session_id="sess-old",
            seller_url="http://seller.example.com",
            created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        manager.store.save(record)

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "session_id": "sess-new-456",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            session_id = await manager.get_or_create_session(
                seller_url="http://seller.example.com",
                buyer_identity={"seat_id": "seat-abc"},
            )

        assert session_id == "sess-new-456"

    @pytest.mark.asyncio
    async def test_send_message(self, manager):
        """Test sending a message on an existing session."""
        # Pre-populate with active session
        record = SessionRecord(
            session_id="sess-msg",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "Here are the available products...",
            "session_id": "sess-msg",
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            response = await manager.send_message(
                seller_url="http://seller.example.com",
                session_id="sess-msg",
                message={"type": "discovery", "content": "List available products"},
            )

        assert response["response"] == "Here are the available products..."

    @pytest.mark.asyncio
    async def test_send_message_handles_404_expired(self, manager):
        """Test that 404 on send_message triggers session recreation."""
        # Pre-populate with session that seller considers expired
        record = SessionRecord(
            session_id="sess-stale",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        # First call returns 404 (session expired on seller side)
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.json.return_value = {"error": "Session not found"}

        # Session creation returns new session
        mock_create = MagicMock()
        mock_create.status_code = 201
        mock_create.json.return_value = {
            "session_id": "sess-renewed",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        }

        # Retry with new session succeeds
        mock_success = MagicMock()
        mock_success.status_code = 200
        mock_success.json.return_value = {
            "response": "Success with new session",
            "session_id": "sess-renewed",
        }

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[mock_404, mock_create, mock_success])

            response = await manager.send_message(
                seller_url="http://seller.example.com",
                session_id="sess-stale",
                message={"type": "discovery", "content": "List products"},
                buyer_identity={"seat_id": "seat-abc"},
            )

        assert response["response"] == "Success with new session"
        # Store should have the new session
        stored = manager.store.get("http://seller.example.com")
        assert stored.session_id == "sess-renewed"

    @pytest.mark.asyncio
    async def test_close_session(self, manager):
        """Test closing a session."""
        record = SessionRecord(
            session_id="sess-close",
            seller_url="http://seller.example.com",
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
        )
        manager.store.save(record)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "closed"}

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            await manager.close_session(
                seller_url="http://seller.example.com",
                session_id="sess-close",
            )

        # Session should be removed from store
        assert manager.store.get("http://seller.example.com") is None

    def test_list_active_sessions(self, manager):
        """Test listing all active sessions."""
        for i in range(3):
            record = SessionRecord(
                session_id=f"sess-{i}",
                seller_url=f"http://seller{i}.example.com",
                created_at=datetime.now(timezone.utc).isoformat(),
                expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            )
            manager.store.save(record)

        # Add an expired one that should be filtered
        expired = SessionRecord(
            session_id="sess-expired",
            seller_url="http://expired.example.com",
            created_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        )
        manager.store.save(expired)

        active = manager.list_active_sessions()
        assert len(active) == 3
        assert "http://expired.example.com" not in active

    @pytest.mark.asyncio
    async def test_create_session_failure_raises(self, manager):
        """Test that failed session creation raises an error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal server error"}
        mock_response.text = "Internal server error"

        with patch("ad_buyer.sessions.session_manager.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)

            with pytest.raises(RuntimeError, match="Failed to create session"):
                await manager.create_session(
                    seller_url="http://seller.example.com",
                    buyer_identity={"seat_id": "seat-abc"},
                )
