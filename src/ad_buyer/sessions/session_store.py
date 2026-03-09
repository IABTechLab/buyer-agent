# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""File-backed session store for persisting active seller sessions."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class SessionRecord:
    """Record of an active session with a seller.

    Attributes:
        session_id: Unique session identifier from the seller.
        seller_url: Base URL of the seller endpoint.
        created_at: ISO 8601 timestamp of session creation.
        expires_at: ISO 8601 timestamp of session expiry (7-day TTL).
    """

    session_id: str
    seller_url: str
    created_at: str
    expires_at: str

    def is_expired(self) -> bool:
        """Check if this session has expired.

        Returns:
            True if the session has passed its expiry time.
        """
        expires = datetime.fromisoformat(self.expires_at)
        # Ensure timezone-aware comparison
        now = datetime.now(timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now >= expires

    def to_dict(self) -> dict:
        """Serialize to a dictionary for JSON storage."""
        return {
            "session_id": self.session_id,
            "seller_url": self.seller_url,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionRecord":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with session record fields.

        Returns:
            A SessionRecord instance.
        """
        return cls(
            session_id=data["session_id"],
            seller_url=data["seller_url"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )


class SessionStore:
    """File-backed store for active seller sessions.

    Persists session records to a JSON file so sessions survive
    process restarts. Keyed by seller_url for quick lookup.

    Args:
        path: File path for the JSON store. Created if it doesn't exist.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._sessions: dict[str, SessionRecord] = {}
        self._load()

    def _load(self) -> None:
        """Load sessions from the store file."""
        if not os.path.exists(self._path):
            self._sessions = {}
            return
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
            self._sessions = {
                url: SessionRecord.from_dict(rec)
                for url, rec in data.items()
            }
        except (json.JSONDecodeError, KeyError):
            self._sessions = {}

    def _save(self) -> None:
        """Write sessions to the store file."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(
                {url: rec.to_dict() for url, rec in self._sessions.items()},
                f,
                indent=2,
            )

    def get(self, seller_url: str) -> Optional[SessionRecord]:
        """Get the active session for a seller, if one exists and is not expired.

        Args:
            seller_url: Base URL of the seller endpoint.

        Returns:
            The SessionRecord if active, or None if not found or expired.
        """
        record = self._sessions.get(seller_url)
        if record is None:
            return None
        if record.is_expired():
            return None
        return record

    def save(self, record: SessionRecord) -> None:
        """Save or update a session record.

        Args:
            record: The SessionRecord to persist.
        """
        self._sessions[record.seller_url] = record
        self._save()

    def remove(self, seller_url: str) -> None:
        """Remove a session for a seller.

        Args:
            seller_url: Base URL of the seller endpoint.
        """
        if seller_url in self._sessions:
            del self._sessions[seller_url]
            self._save()

    def list_sessions(self) -> dict[str, str]:
        """List all stored sessions (including expired, for inspection).

        Returns:
            Dictionary of seller_url to session_id.
        """
        return {url: rec.session_id for url, rec in self._sessions.items()}

    def cleanup_expired(self) -> int:
        """Remove all expired sessions from the store.

        Returns:
            Number of expired sessions removed.
        """
        expired_urls = [
            url for url, rec in self._sessions.items() if rec.is_expired()
        ]
        for url in expired_urls:
            del self._sessions[url]
        if expired_urls:
            self._save()
        return len(expired_urls)
