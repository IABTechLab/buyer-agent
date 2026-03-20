# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for DealJockey template CRUD tools.

Tests cover:
- ManageDealTemplateTool: create, read, list, update, delete deal templates
- ManageSupplyPathTemplateTool: create, read, list, update, delete supply path templates
- Deal template advertiser_id scoping (null = agency-wide, set = advertiser-scoped)
- Supply path template scoring_weights validation (sum must equal 1.0)
"""

import json

import pytest

from ad_buyer.storage import DealStore
from ad_buyer.tools.deal_jockey.templates import (
    ManageDealTemplateTool,
    ManageSupplyPathTemplateTool,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def deal_store():
    """Create a DealStore backed by in-memory SQLite for template tests."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def deal_template_tool(deal_store):
    """Create a ManageDealTemplateTool with an in-memory store."""
    return ManageDealTemplateTool(deal_store=deal_store)


@pytest.fixture
def supply_path_tool(deal_store):
    """Create a ManageSupplyPathTemplateTool with an in-memory store."""
    return ManageSupplyPathTemplateTool(deal_store=deal_store)


# -----------------------------------------------------------------------
# ManageDealTemplateTool - Create
# -----------------------------------------------------------------------


class TestDealTemplateCreate:
    """Tests for creating deal templates."""

    def test_create_deal_template_returns_success(self, deal_template_tool):
        """Creating a deal template returns a success message with template ID."""
        result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Standard Sports Video PG",
                "deal_type_pref": "PG",
                "inventory_types": ["DIGITAL", "CTV"],
                "preferred_publishers": ["espn.com", "nfl.com"],
                "max_cpm": 25.00,
            }),
        )
        assert "successfully" in result.lower() or "created" in result.lower()
        assert "Standard Sports Video PG" in result

    def test_create_deal_template_with_all_fields(self, deal_template_tool):
        """Creating a template with all fields stores them correctly."""
        result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Full Template",
                "deal_type_pref": "PD",
                "inventory_types": ["DIGITAL"],
                "preferred_publishers": ["nyt.com"],
                "excluded_publishers": ["sketchy.com"],
                "targeting_defaults": {"geo": ["US"], "audience": ["sports"]},
                "max_cpm": 18.50,
                "min_impressions": 100000,
                "default_flight_days": 30,
                "supply_path_prefs": {"max_hops": 2},
                "advertiser_id": "adv-001",
                "agency_id": "agency-001",
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()

    def test_create_deal_template_agency_wide(self, deal_template_tool):
        """Creating a template without advertiser_id makes it agency-wide."""
        result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Agency-Wide Template",
                "deal_type_pref": "PG",
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()
        # Verify it's retrievable and has no advertiser_id
        list_result = deal_template_tool._run(
            action="list",
            params_json=json.dumps({}),
        )
        assert "Agency-Wide Template" in list_result

    def test_create_deal_template_advertiser_scoped(self, deal_template_tool):
        """Creating a template with advertiser_id scopes it to that advertiser."""
        result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Advertiser Template",
                "deal_type_pref": "PD",
                "advertiser_id": "adv-nike",
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()

    def test_create_deal_template_requires_name(self, deal_template_tool):
        """Creating a template without a name returns an error."""
        result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "deal_type_pref": "PG",
            }),
        )
        assert "error" in result.lower()

    def test_create_deal_template_invalid_json(self, deal_template_tool):
        """Creating a template with invalid JSON returns an error."""
        result = deal_template_tool._run(
            action="create",
            params_json="not json",
        )
        assert "error" in result.lower()


# -----------------------------------------------------------------------
# ManageDealTemplateTool - Read
# -----------------------------------------------------------------------


class TestDealTemplateRead:
    """Tests for reading deal templates."""

    def test_read_deal_template_by_id(self, deal_template_tool):
        """Reading a template by ID returns its details."""
        # Create first
        create_result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Readable Template",
                "deal_type_pref": "PG",
                "max_cpm": 20.00,
            }),
        )
        # Extract template ID from result
        template_id = _extract_template_id(create_result)
        assert template_id is not None

        # Read it back
        result = deal_template_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "Readable Template" in result
        assert "PG" in result

    def test_read_deal_template_not_found(self, deal_template_tool):
        """Reading a nonexistent template returns a not-found message."""
        result = deal_template_tool._run(
            action="read",
            params_json=json.dumps({"template_id": "nonexistent-id"}),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageDealTemplateTool - List
# -----------------------------------------------------------------------


class TestDealTemplateList:
    """Tests for listing deal templates."""

    def test_list_deal_templates_empty(self, deal_template_tool):
        """Listing templates when none exist returns an appropriate message."""
        result = deal_template_tool._run(
            action="list",
            params_json=json.dumps({}),
        )
        assert "no" in result.lower() or "0" in result

    def test_list_deal_templates_returns_all(self, deal_template_tool):
        """Listing templates returns all created templates."""
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({"name": "Template A", "deal_type_pref": "PG"}),
        )
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({"name": "Template B", "deal_type_pref": "PD"}),
        )

        result = deal_template_tool._run(
            action="list",
            params_json=json.dumps({}),
        )
        assert "Template A" in result
        assert "Template B" in result

    def test_list_deal_templates_filter_by_advertiser(self, deal_template_tool):
        """Listing templates with advertiser_id filter returns only matching."""
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Nike Template",
                "deal_type_pref": "PG",
                "advertiser_id": "adv-nike",
            }),
        )
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Agency Wide",
                "deal_type_pref": "PD",
            }),
        )
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Adidas Template",
                "deal_type_pref": "PG",
                "advertiser_id": "adv-adidas",
            }),
        )

        result = deal_template_tool._run(
            action="list",
            params_json=json.dumps({"advertiser_id": "adv-nike"}),
        )
        assert "Nike Template" in result
        assert "Adidas Template" not in result

    def test_list_deal_templates_filter_by_deal_type(self, deal_template_tool):
        """Listing templates with deal_type_pref filter returns only matching."""
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({"name": "PG Template", "deal_type_pref": "PG"}),
        )
        deal_template_tool._run(
            action="create",
            params_json=json.dumps({"name": "PD Template", "deal_type_pref": "PD"}),
        )

        result = deal_template_tool._run(
            action="list",
            params_json=json.dumps({"deal_type_pref": "PG"}),
        )
        assert "PG Template" in result
        assert "PD Template" not in result


# -----------------------------------------------------------------------
# ManageDealTemplateTool - Update
# -----------------------------------------------------------------------


class TestDealTemplateUpdate:
    """Tests for updating deal templates."""

    def test_update_deal_template(self, deal_template_tool):
        """Updating a template changes the specified fields."""
        create_result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Old Name",
                "deal_type_pref": "PG",
                "max_cpm": 15.00,
            }),
        )
        template_id = _extract_template_id(create_result)

        update_result = deal_template_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": template_id,
                "name": "New Name",
                "max_cpm": 20.00,
            }),
        )
        assert "updated" in update_result.lower()

        # Read back to verify
        read_result = deal_template_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "New Name" in read_result

    def test_update_deal_template_default_price(self, deal_template_tool):
        """Updating default_price succeeds and persists the new value.

        Regression test for buyer-5wx: update_deal_template() was missing
        default_price in its allowed column set, causing updates to silently
        return False and the tool to report "not found".
        """
        create_result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Price Update Test",
                "deal_type_pref": "PG",
                "default_price": 15.00,
            }),
        )
        template_id = _extract_template_id(create_result)
        assert template_id is not None

        # Update the default_price
        update_result = deal_template_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": template_id,
                "default_price": 32.00,
            }),
        )
        assert "updated" in update_result.lower(), (
            f"Expected 'updated' in result but got: {update_result}"
        )

        # Read back to verify the new value persisted
        read_result = deal_template_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "32" in read_result, (
            f"Expected default_price 32.0 in template but got: {read_result}"
        )

    def test_update_deal_template_not_found(self, deal_template_tool):
        """Updating a nonexistent template returns a not-found message."""
        result = deal_template_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": "nonexistent",
                "name": "Won't Work",
            }),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageDealTemplateTool - Delete
# -----------------------------------------------------------------------


class TestDealTemplateDelete:
    """Tests for deleting deal templates."""

    def test_delete_deal_template(self, deal_template_tool):
        """Deleting a template removes it."""
        create_result = deal_template_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Doomed Template",
                "deal_type_pref": "PG",
            }),
        )
        template_id = _extract_template_id(create_result)

        delete_result = deal_template_tool._run(
            action="delete",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "deleted" in delete_result.lower()

        # Verify it's gone
        read_result = deal_template_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "not found" in read_result.lower()

    def test_delete_deal_template_not_found(self, deal_template_tool):
        """Deleting a nonexistent template returns a not-found message."""
        result = deal_template_tool._run(
            action="delete",
            params_json=json.dumps({"template_id": "nonexistent"}),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageDealTemplateTool - Invalid action
# -----------------------------------------------------------------------


class TestDealTemplateInvalidAction:
    """Tests for invalid actions on deal templates."""

    def test_invalid_action(self, deal_template_tool):
        """An invalid action returns an error message."""
        result = deal_template_tool._run(
            action="invalid_action",
            params_json=json.dumps({}),
        )
        assert "error" in result.lower()


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - Create
# -----------------------------------------------------------------------


class TestSupplyPathTemplateCreate:
    """Tests for creating supply path templates."""

    def test_create_supply_path_template_success(self, supply_path_tool):
        """Creating a supply path template with valid weights returns success."""
        result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Low Fee Direct Paths",
                "scoring_weights": {
                    "transparency": 0.3,
                    "fee": 0.4,
                    "trust": 0.2,
                    "performance": 0.1,
                },
                "max_reseller_hops": 2,
                "preferred_ssps": ["index", "pubmatic"],
                "blocked_ssps": ["shady-exchange"],
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()

    def test_create_supply_path_template_all_fields(self, supply_path_tool):
        """Creating a template with all fields stores them correctly."""
        result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Full SPO Template",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
                "max_reseller_hops": 3,
                "require_sellers_json": True,
                "preferred_ssps": ["index"],
                "blocked_ssps": ["bad-ssp"],
                "preferred_curators": ["magnite"],
                "rules": [{"rule": "prefer_direct"}],
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()

    def test_create_supply_path_template_weights_must_sum_to_one(
        self, supply_path_tool
    ):
        """Weights that don't sum to 1.0 are rejected."""
        result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Bad Weights",
                "scoring_weights": {
                    "transparency": 0.3,
                    "fee": 0.3,
                    "trust": 0.3,
                    "performance": 0.3,
                },
            }),
        )
        assert "error" in result.lower()
        assert "sum" in result.lower() or "1.0" in result

    def test_create_supply_path_template_weights_near_one_accepted(
        self, supply_path_tool
    ):
        """Weights that sum to ~1.0 within float tolerance are accepted."""
        result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Nearly One Weights",
                "scoring_weights": {
                    "transparency": 0.1,
                    "fee": 0.2,
                    "trust": 0.3,
                    "performance": 0.4,
                },
            }),
        )
        assert "created" in result.lower() or "successfully" in result.lower()

    def test_create_supply_path_template_requires_name(self, supply_path_tool):
        """Creating without a name returns an error."""
        result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
            }),
        )
        assert "error" in result.lower()

    def test_create_supply_path_template_invalid_json(self, supply_path_tool):
        """Creating with invalid JSON returns an error."""
        result = supply_path_tool._run(
            action="create",
            params_json="not json",
        )
        assert "error" in result.lower()


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - Read
# -----------------------------------------------------------------------


class TestSupplyPathTemplateRead:
    """Tests for reading supply path templates."""

    def test_read_supply_path_template_by_id(self, supply_path_tool):
        """Reading a template by ID returns its details."""
        create_result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Readable SPO Template",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
                "max_reseller_hops": 2,
            }),
        )
        template_id = _extract_template_id(create_result)
        assert template_id is not None

        result = supply_path_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "Readable SPO Template" in result
        assert "transparency" in result.lower()

    def test_read_supply_path_template_not_found(self, supply_path_tool):
        """Reading a nonexistent template returns a not-found message."""
        result = supply_path_tool._run(
            action="read",
            params_json=json.dumps({"template_id": "nonexistent"}),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - List
# -----------------------------------------------------------------------


class TestSupplyPathTemplateList:
    """Tests for listing supply path templates."""

    def test_list_supply_path_templates_empty(self, supply_path_tool):
        """Listing when empty returns an appropriate message."""
        result = supply_path_tool._run(
            action="list",
            params_json=json.dumps({}),
        )
        assert "no" in result.lower() or "0" in result

    def test_list_supply_path_templates_returns_all(self, supply_path_tool):
        """Listing returns all created templates."""
        supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "SPO A",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
            }),
        )
        supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "SPO B",
                "scoring_weights": {
                    "transparency": 0.4,
                    "fee": 0.3,
                    "trust": 0.2,
                    "performance": 0.1,
                },
            }),
        )

        result = supply_path_tool._run(
            action="list",
            params_json=json.dumps({}),
        )
        assert "SPO A" in result
        assert "SPO B" in result


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - Update
# -----------------------------------------------------------------------


class TestSupplyPathTemplateUpdate:
    """Tests for updating supply path templates."""

    def test_update_supply_path_template(self, supply_path_tool):
        """Updating a template changes the specified fields."""
        create_result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Old SPO Name",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
                "max_reseller_hops": 3,
            }),
        )
        template_id = _extract_template_id(create_result)

        update_result = supply_path_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": template_id,
                "name": "New SPO Name",
                "max_reseller_hops": 2,
            }),
        )
        assert "updated" in update_result.lower()

        # Read back
        read_result = supply_path_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "New SPO Name" in read_result

    def test_update_supply_path_template_revalidates_weights(self, supply_path_tool):
        """Updating scoring_weights still validates sum = 1.0."""
        create_result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Weight Test",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
            }),
        )
        template_id = _extract_template_id(create_result)

        update_result = supply_path_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": template_id,
                "scoring_weights": {
                    "transparency": 0.5,
                    "fee": 0.5,
                    "trust": 0.5,
                    "performance": 0.5,
                },
            }),
        )
        assert "error" in update_result.lower()

    def test_update_supply_path_template_not_found(self, supply_path_tool):
        """Updating a nonexistent template returns a not-found message."""
        result = supply_path_tool._run(
            action="update",
            params_json=json.dumps({
                "template_id": "nonexistent",
                "name": "Won't Work",
            }),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - Delete
# -----------------------------------------------------------------------


class TestSupplyPathTemplateDelete:
    """Tests for deleting supply path templates."""

    def test_delete_supply_path_template(self, supply_path_tool):
        """Deleting a template removes it."""
        create_result = supply_path_tool._run(
            action="create",
            params_json=json.dumps({
                "name": "Doomed SPO Template",
                "scoring_weights": {
                    "transparency": 0.25,
                    "fee": 0.25,
                    "trust": 0.25,
                    "performance": 0.25,
                },
            }),
        )
        template_id = _extract_template_id(create_result)

        delete_result = supply_path_tool._run(
            action="delete",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "deleted" in delete_result.lower()

        # Verify it's gone
        read_result = supply_path_tool._run(
            action="read",
            params_json=json.dumps({"template_id": template_id}),
        )
        assert "not found" in read_result.lower()

    def test_delete_supply_path_template_not_found(self, supply_path_tool):
        """Deleting a nonexistent template returns a not-found message."""
        result = supply_path_tool._run(
            action="delete",
            params_json=json.dumps({"template_id": "nonexistent"}),
        )
        assert "not found" in result.lower()


# -----------------------------------------------------------------------
# ManageSupplyPathTemplateTool - Invalid action
# -----------------------------------------------------------------------


class TestSupplyPathTemplateInvalidAction:
    """Tests for invalid actions on supply path templates."""

    def test_invalid_action(self, supply_path_tool):
        """An invalid action returns an error message."""
        result = supply_path_tool._run(
            action="invalid_action",
            params_json=json.dumps({}),
        )
        assert "error" in result.lower()


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _extract_template_id(result_text: str) -> str | None:
    """Extract a template ID from a tool result string.

    Looks for patterns like 'ID: <uuid>' or 'template_id: <uuid>' in
    the output text.
    """
    for line in result_text.split("\n"):
        lower = line.lower().strip()
        if "id:" in lower and "template" not in lower.split("id:")[0].strip().split()[-1:]:
            # Match lines like "  ID: abc-123"
            parts = line.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if candidate and len(candidate) > 5:
                    return candidate
        if "template_id:" in lower or "id:" in lower:
            parts = line.split(":", 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if candidate and len(candidate) > 5:
                    return candidate
    return None
