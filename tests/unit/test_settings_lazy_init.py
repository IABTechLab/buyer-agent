# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for lazy Settings instantiation.

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

import pytest

_SETTINGS_MOD = "ad_buyer.config.settings"


@pytest.fixture(autouse=True)
def _restore_settings_module():
    """Restore the original settings module after each test in this file.

    The tests below ``del sys.modules[...]`` and re-import the settings module
    to exercise lazy init with an empty cache. Without restoration the rebuilt
    module leaks into ``sys.modules``, so later tests in the full suite that do
    ``from ad_buyer.config.settings import settings`` bind to a fresh lazy
    proxy reading live env — which made
    ``test_real_model_path_e2e::test_label_reflects_active_mode`` fail only in
    suite order. Snapshot and restore the original module object here so the
    reload stays contained to this file.
    """
    original = sys.modules.get(_SETTINGS_MOD)
    try:
        yield
    finally:
        if original is not None:
            sys.modules[_SETTINGS_MOD] = original


def _reload_settings_module():
    """Force a fresh import of the settings module so its lru_cache is empty."""
    if _SETTINGS_MOD in sys.modules:
        del sys.modules[_SETTINGS_MOD]
    return importlib.import_module(_SETTINGS_MOD)


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
