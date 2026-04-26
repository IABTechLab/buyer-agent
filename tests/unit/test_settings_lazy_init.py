# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for lazy Settings instantiation (ar-le3).

The buyer used to instantiate `settings = Settings()` at module top, freezing
environment variables before tests could override them. The fix replaces that
with a `_LazySettings` proxy backed by a cached `get_settings()` factory, so
Settings is constructed on first attribute access rather than at import time.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import patch


def _reload_settings_module():
    """Force a fresh import of the settings module so its lru_cache is empty."""
    mod_name = "ad_buyer.config.settings"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_importing_settings_does_not_construct_eagerly():
    """Importing the module must not call Settings() at import time."""
    settings_mod = _reload_settings_module()

    # Cache should be empty: get_settings() has not been invoked yet.
    info = settings_mod.get_settings.cache_info()
    assert info.hits == 0
    assert info.misses == 0
    assert info.currsize == 0

    # The module-level `settings` should be the lazy proxy, not a Settings.
    assert isinstance(settings_mod.settings, settings_mod._LazySettings)


def test_env_override_before_first_access_is_seen():
    """Env vars set after import but before first attribute access take effect."""
    settings_mod = _reload_settings_module()

    # Sanity: still uninstantiated.
    assert settings_mod.get_settings.cache_info().currsize == 0

    # Override an env var BEFORE touching settings.X.
    with patch.dict(os.environ, {"EMBEDDING_MODE": "mock"}, clear=False):
        # First attribute access constructs Settings with current env.
        assert settings_mod.settings.embedding_mode == "mock"

    # Cache populated after first access.
    assert settings_mod.get_settings.cache_info().currsize == 1


def test_existing_call_sites_still_work():
    """Smoke check: existing `settings.X` access patterns still resolve."""
    settings_mod = _reload_settings_module()

    # These mirror real call sites scattered across the buyer codebase.
    assert settings_mod.settings.embedding_mode in {
        "mock",
        "local",
        "advertiser",
        "hybrid",
    }
    assert isinstance(settings_mod.settings.default_llm_model, str)
    assert isinstance(settings_mod.settings.crew_verbose, bool)
    # Methods on the underlying Settings instance proxy through too.
    assert isinstance(settings_mod.settings.get_cors_origins(), list)


def test_get_settings_returns_cached_instance():
    """get_settings() is lru_cached, so repeated calls return the same object."""
    settings_mod = _reload_settings_module()
    a = settings_mod.get_settings()
    b = settings_mod.get_settings()
    assert a is b
