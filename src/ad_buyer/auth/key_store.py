# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Persistent API key storage for seller endpoints.

Keys are stored per seller URL in a JSON file.  Values are
base64-encoded to avoid storing raw secrets as plaintext on disk.
This is *not* encryption — it is a basic obfuscation layer that
prevents accidental exposure in casual file reads.  For production
deployments, back the store with a secrets manager or encrypted
file system.
"""

import base64
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path.home() / ".ad_buyer" / "seller_keys.json"


class ApiKeyStore:
    """File-backed API key storage keyed by seller URL.

    Keys are base64-encoded on disk so that raw secret values do not
    appear in plaintext in the JSON file.

    Args:
        store_path: Path to the JSON file used for persistence.
            Defaults to ``~/.ad_buyer/seller_keys.json``.
    """

    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path or _DEFAULT_STORE_PATH
        self._keys: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_key(self, seller_url: str, api_key: str) -> None:
        """Store (or replace) an API key for *seller_url*."""
        url = self._normalize_url(seller_url)
        self._keys[url] = api_key
        self._save()

    def get_key(self, seller_url: str) -> str | None:
        """Return the API key for *seller_url*, or ``None``."""
        url = self._normalize_url(seller_url)
        return self._keys.get(url)

    def remove_key(self, seller_url: str) -> bool:
        """Remove the key for *seller_url*.  Returns ``True`` if it existed."""
        url = self._normalize_url(seller_url)
        if url in self._keys:
            del self._keys[url]
            self._save()
            return True
        return False

    def rotate_key(self, seller_url: str, new_key: str) -> None:
        """Replace the key for *seller_url* (or add if new)."""
        self.add_key(seller_url, new_key)

    def list_sellers(self) -> list[str]:
        """Return all seller URLs that have a stored key."""
        return list(self._keys.keys())

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load keys from disk, decoding base64 values."""
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._keys = {
                url: base64.b64decode(encoded.encode()).decode() for url, encoded in raw.items()
            }
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError, Exception) as exc:
            logger.warning("Could not load key store from %s: %s", self._path, exc)
            self._keys = {}

    def _save(self) -> None:
        """Persist keys to disk, encoding values as base64."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encoded = {url: base64.b64encode(key.encode()).decode() for url, key in self._keys.items()}
        self._path.write_text(
            json.dumps(encoded, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Strip trailing slashes for consistent key lookup."""
        return url.rstrip("/")
