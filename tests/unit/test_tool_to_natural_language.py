# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for UnifiedClient._tool_to_natural_language registry mapping.

These tests guard against the prior brittle if/elif chain by asserting:
- Every registered tool produces a non-empty string.
- Unknown / renamed tools fall back to a sensible non-empty message
  rather than raising or returning empty.
- Lookup is case-insensitive on the tool name.
- Args are reflected in the output for arg-bearing tools.
"""

from __future__ import annotations

import pytest

from ad_buyer.clients.unified_client import UnifiedClient


@pytest.fixture
def client() -> UnifiedClient:
    return UnifiedClient(base_url="http://test.test")


# ---------------------------------------------------------------------------
# Registry coverage: every registered tool returns a non-empty string.
# ---------------------------------------------------------------------------

def test_every_registered_tool_has_non_empty_mapping(client: UnifiedClient):
    """Each entry in _TOOL_NL_REGISTRY must produce a non-empty string."""
    sample_args = {
        "name": "Sample",
        "type": "advertiser",
        "accountId": "acct-1",
        "orderId": "ord-1",
        "productId": "prod-1",
        "budget": 1000,
        "quantity": 1000,
        "id": "x-1",
    }

    for tool_name, entry in UnifiedClient._TOOL_NL_REGISTRY.items():
        # Static (no-arg) entry path
        no_arg_msg = client._tool_to_natural_language(tool_name, {})
        assert isinstance(no_arg_msg, str) and no_arg_msg, (
            f"Empty/no-arg mapping for registered tool {tool_name!r}"
        )

        # With-arg path (use callable entry directly, or generic fallback for
        # static entries when args are present).
        with_arg_msg = client._tool_to_natural_language(tool_name, sample_args)
        assert isinstance(with_arg_msg, str) and with_arg_msg, (
            f"Empty with-args mapping for registered tool {tool_name!r}"
        )
        # Sanity: callable-backed entries should differ from no-arg fallback
        if callable(entry):
            assert with_arg_msg != "", (
                f"Callable entry returned empty for {tool_name!r}"
            )


# ---------------------------------------------------------------------------
# Unknown tool fallback: never empty, never raises.
# ---------------------------------------------------------------------------

def test_unknown_tool_returns_generic_fallback(client: UnifiedClient):
    msg = client._tool_to_natural_language("totally_made_up_tool", {})
    assert msg
    assert "totally_made_up_tool" in msg
    assert msg.lower().startswith("execute")


def test_unknown_tool_with_args_includes_args(client: UnifiedClient):
    msg = client._tool_to_natural_language(
        "totally_made_up_tool", {"foo": "bar", "n": 3}
    )
    assert "totally_made_up_tool" in msg
    assert "foo=bar" in msg
    assert "n=3" in msg


def test_renamed_tool_does_not_silently_produce_empty(client: UnifiedClient):
    """If a Tool is renamed (or typo'd) the function must still return a
    non-empty descriptive string instead of '' or raising."""
    msg = client._tool_to_natural_language("list_productz", {})
    assert msg
    assert "list_productz" in msg


def test_empty_tool_name_is_safe(client: UnifiedClient):
    msg = client._tool_to_natural_language("", {})
    assert msg  # non-empty
    # Should not raise; should be a generic execute-style message
    assert "execute" in msg.lower()


def test_none_args_is_safe(client: UnifiedClient):
    # None args is a documented input on the public call_tool path.
    msg = client._tool_to_natural_language("list_products", None)
    assert msg
    assert "List all available advertising products" in msg


# ---------------------------------------------------------------------------
# Case insensitivity.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["list_products", "LIST_PRODUCTS", "List_Products", "  list_products  "],
)
def test_case_insensitive_lookup(client: UnifiedClient, name: str):
    msg = client._tool_to_natural_language(name, {})
    assert "List all available advertising products" in msg


# ---------------------------------------------------------------------------
# Backward-compatible behavior with the previous test suite expectations.
# ---------------------------------------------------------------------------

def test_create_account_includes_name_and_type(client: UnifiedClient):
    msg = client._tool_to_natural_language(
        "create_account", {"name": "TestCo", "type": "advertiser"}
    )
    assert "TestCo" in msg
    assert "advertiser" in msg


def test_create_order_includes_name_and_formatted_budget(client: UnifiedClient):
    msg = client._tool_to_natural_language(
        "create_order",
        {"name": "Q1 Campaign", "accountId": "acct-1", "budget": 50000},
    )
    assert "Q1 Campaign" in msg
    assert "50,000" in msg


def test_create_line_includes_quantity_with_thousands_separator(
    client: UnifiedClient,
):
    msg = client._tool_to_natural_language(
        "create_line",
        {
            "name": "Line A",
            "orderId": "ord-1",
            "productId": "prod-1",
            "quantity": 1234567,
        },
    )
    assert "1,234,567" in msg
    assert "Line A" in msg


def test_get_by_id_tools_render_id(client: UnifiedClient):
    for tool in ("get_product", "get_account", "get_order"):
        msg = client._tool_to_natural_language(tool, {"id": "abc-123"})
        assert "abc-123" in msg


# ---------------------------------------------------------------------------
# Static-entry-with-args falls through to generic renderer (so args are
# preserved) but still returns a useful, non-empty message.
# ---------------------------------------------------------------------------

def test_listing_tool_with_args_falls_through_to_generic(client: UnifiedClient):
    msg = client._tool_to_natural_language(
        "list_orders", {"accountId": "acct-9"}
    )
    assert msg
    # Should mention either the tool name or the arg; generic renderer does both
    assert "list_orders" in msg
    assert "accountId=acct-9" in msg
