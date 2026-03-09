# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Session manager for multi-turn buyer-seller conversations.

Handles creating, reusing, and renewing sessions with seller endpoints.
Sessions follow a 7-day TTL and are automatically recreated on expiry.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .session_store import SessionRecord, SessionStore

logger = logging.getLogger(__name__)

# Default session store location
DEFAULT_STORE_PATH = os.path.join(
    os.path.expanduser("~"), ".ad_buyer", "sessions.json"
)

# Default session TTL (7 days), used as fallback when seller doesn't specify
SESSION_TTL_DAYS = 7

# HTTP timeout for session API calls
HTTP_TIMEOUT = 30.0


class SessionManager:
    """Manages multi-turn sessions with seller endpoints.

    Provides session lifecycle management: create, reuse, send messages,
    handle expiry, and close. Sessions are persisted to disk so they
    survive process restarts.

    Args:
        store_path: Path to the JSON file for session persistence.
            Defaults to ~/.ad_buyer/sessions.json.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        store_path: str = DEFAULT_STORE_PATH,
        timeout: float = HTTP_TIMEOUT,
    ) -> None:
        self.store = SessionStore(store_path)
        self._timeout = timeout

    async def create_session(
        self,
        seller_url: str,
        buyer_identity: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a new session with a seller.

        Posts to the seller's /sessions endpoint to establish a session.

        Args:
            seller_url: Base URL of the seller endpoint.
            buyer_identity: Dictionary with buyer identity info
                (seat_id, name, agency_id, etc.).

        Returns:
            The new session_id.

        Raises:
            RuntimeError: If the seller rejects the session creation.
        """
        url = f"{seller_url.rstrip('/')}/sessions"
        payload: dict[str, Any] = {}
        if buyer_identity:
            payload["buyer_identity"] = buyer_identity

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create session with {seller_url}: "
                f"{response.status_code} - {response.text}"
            )

        data = response.json()
        session_id = data["session_id"]
        created_at = data.get(
            "created_at", datetime.now(timezone.utc).isoformat()
        )
        expires_at = data.get(
            "expires_at",
            (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
        )

        record = SessionRecord(
            session_id=session_id,
            seller_url=seller_url,
            created_at=created_at,
            expires_at=expires_at,
        )
        self.store.save(record)
        logger.info("Created session %s with %s", session_id, seller_url)
        return session_id

    async def get_or_create_session(
        self,
        seller_url: str,
        buyer_identity: Optional[dict[str, Any]] = None,
    ) -> str:
        """Get an existing active session or create a new one.

        Checks the local store for an active (non-expired) session with the
        given seller. If found, returns it. Otherwise, creates a new session.

        Args:
            seller_url: Base URL of the seller endpoint.
            buyer_identity: Dictionary with buyer identity info.

        Returns:
            The session_id (existing or newly created).
        """
        existing = self.store.get(seller_url)
        if existing is not None:
            logger.debug("Reusing session %s for %s", existing.session_id, seller_url)
            return existing.session_id

        logger.info("No active session for %s, creating new one", seller_url)
        return await self.create_session(seller_url, buyer_identity)

    async def send_message(
        self,
        seller_url: str,
        session_id: str,
        message: dict[str, Any],
        buyer_identity: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send a message on an existing session.

        Posts to the seller's /sessions/{id}/messages endpoint. If the seller
        returns 404 (session expired), automatically creates a new session
        and retries the message.

        Args:
            seller_url: Base URL of the seller endpoint.
            session_id: The session ID to send on.
            message: Message payload (type, content, etc.).
            buyer_identity: Buyer identity for session recreation if needed.

        Returns:
            The seller's response as a dictionary.

        Raises:
            RuntimeError: If the message fails after retry.
        """
        url = f"{seller_url.rstrip('/')}/sessions/{session_id}/messages"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=message)

            # Handle 404 — session expired on seller side
            if response.status_code == 404:
                logger.warning(
                    "Session %s expired on %s, creating new session",
                    session_id,
                    seller_url,
                )
                self.store.remove(seller_url)

                # Create new session
                new_session_id = await self._create_session_with_client(
                    client, seller_url, buyer_identity
                )

                # Retry the message with the new session
                retry_url = f"{seller_url.rstrip('/')}/sessions/{new_session_id}/messages"
                response = await client.post(retry_url, json=message)

            if response.status_code not in (200, 201):
                raise RuntimeError(
                    f"Failed to send message on session {session_id}: "
                    f"{response.status_code}"
                )

        return response.json()

    async def _create_session_with_client(
        self,
        client: httpx.AsyncClient,
        seller_url: str,
        buyer_identity: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a session using an existing httpx client instance.

        Internal helper to avoid creating a new client when retrying
        within send_message.

        Args:
            client: An existing httpx.AsyncClient.
            seller_url: Base URL of the seller endpoint.
            buyer_identity: Buyer identity info.

        Returns:
            The new session_id.

        Raises:
            RuntimeError: If session creation fails.
        """
        url = f"{seller_url.rstrip('/')}/sessions"
        payload: dict[str, Any] = {}
        if buyer_identity:
            payload["buyer_identity"] = buyer_identity

        response = await client.post(url, json=payload)

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create session with {seller_url}: "
                f"{response.status_code}"
            )

        data = response.json()
        session_id = data["session_id"]
        created_at = data.get(
            "created_at", datetime.now(timezone.utc).isoformat()
        )
        expires_at = data.get(
            "expires_at",
            (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
        )

        record = SessionRecord(
            session_id=session_id,
            seller_url=seller_url,
            created_at=created_at,
            expires_at=expires_at,
        )
        self.store.save(record)
        logger.info("Created session %s with %s", session_id, seller_url)
        return session_id

    async def close_session(
        self,
        seller_url: str,
        session_id: str,
    ) -> None:
        """Close a session with a seller.

        Posts to the seller's /sessions/{id}/close endpoint and removes
        the session from the local store.

        Args:
            seller_url: Base URL of the seller endpoint.
            session_id: The session ID to close.
        """
        url = f"{seller_url.rstrip('/')}/sessions/{session_id}/close"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                await client.post(url)
        except Exception:
            logger.warning(
                "Failed to close session %s on %s (may already be expired)",
                session_id,
                seller_url,
            )

        self.store.remove(seller_url)
        logger.info("Closed session %s with %s", session_id, seller_url)

    def list_active_sessions(self) -> dict[str, str]:
        """List all active (non-expired) sessions.

        Returns:
            Dictionary of seller_url to session_id for active sessions only.
        """
        result: dict[str, str] = {}
        for seller_url, session_id in self.store.list_sessions().items():
            record = self.store.get(seller_url)
            if record is not None:  # get() filters expired
                result[seller_url] = record.session_id
        return result
