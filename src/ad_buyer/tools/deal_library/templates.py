# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Template management tools for DealLibrary.

CrewAI tools for CRUD operations on deal templates and supply path
optimization templates.  Deal templates encode an agency's preferred
terms for common deal types (Strategic Plan Section 6.3).  Supply path
templates codify SPO routing preferences with weighted scoring
(Strategic Plan Section 6.4).

Usage:
    store = DealStore("sqlite:///./ad_buyer.db")
    store.connect()

    deal_tmpl = ManageDealTemplateTool(deal_store=store)
    result = deal_tmpl._run(
        action="create",
        params_json='{"name": "Sports PG", "deal_type_pref": "PG"}',
    )

    spo_tmpl = ManageSupplyPathTemplateTool(deal_store=store)
    result = spo_tmpl._run(
        action="create",
        params_json='{"name": "Direct Paths", "scoring_weights": {...}}',
    )
"""

import json
import logging
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...storage.deal_store import DealStore

logger = logging.getLogger(__name__)

# Valid actions for both tools
VALID_ACTIONS = {"create", "read", "list", "update", "delete"}


# -- Input schemas -----------------------------------------------------------


class ManageDealTemplateInput(BaseModel):
    """Input schema for ManageDealTemplateTool."""

    action: str = Field(
        ...,
        description=(
            "The CRUD action to perform: 'create', 'read', 'list', "
            "'update', or 'delete'."
        ),
    )
    params_json: str = Field(
        ...,
        description=(
            "JSON string with action parameters. "
            "create: name (required), deal_type_pref, inventory_types, "
            "preferred_publishers, excluded_publishers, targeting_defaults, "
            "default_price, max_cpm, min_impressions, default_flight_days, "
            "supply_path_prefs, advertiser_id, agency_id. "
            "read: template_id (required). "
            "list: advertiser_id (optional), deal_type_pref (optional). "
            "update: template_id (required), plus fields to update. "
            "delete: template_id (required)."
        ),
    )


class ManageSupplyPathTemplateInput(BaseModel):
    """Input schema for ManageSupplyPathTemplateTool."""

    action: str = Field(
        ...,
        description=(
            "The CRUD action to perform: 'create', 'read', 'list', "
            "'update', or 'delete'."
        ),
    )
    params_json: str = Field(
        ...,
        description=(
            "JSON string with action parameters. "
            "create: name (required), scoring_weights (dict with "
            "transparency/fee/trust/performance keys, must sum to 1.0), "
            "max_reseller_hops, require_sellers_json, preferred_ssps, "
            "blocked_ssps, preferred_curators, rules. "
            "read: template_id (required). "
            "list: {} (no filters). "
            "update: template_id (required), plus fields to update. "
            "delete: template_id (required)."
        ),
    )


# -- Validation helpers ------------------------------------------------------


def _validate_scoring_weights(weights: dict[str, float]) -> list[str]:
    """Validate SPO scoring weights.

    Checks that all four required keys are present and that values
    sum to 1.0 (within floating-point tolerance).

    Args:
        weights: Dict with transparency, fee, trust, performance keys.

    Returns:
        List of error messages (empty if valid).
    """
    errors: list[str] = []
    required_keys = {"transparency", "fee", "trust", "performance"}
    missing = required_keys - set(weights.keys())
    if missing:
        errors.append(
            f"Missing scoring weight keys: {', '.join(sorted(missing))}. "
            f"Required: transparency, fee, trust, performance."
        )
        return errors

    # Validate values are numeric
    for key in required_keys:
        val = weights.get(key)
        if not isinstance(val, (int, float)):
            errors.append(f"Scoring weight '{key}' must be a number, got {type(val).__name__}.")

    if errors:
        return errors

    # Validate sum = 1.0 (within tolerance)
    total = sum(weights[k] for k in required_keys)
    if abs(total - 1.0) > 0.01:
        errors.append(
            f"Scoring weights must sum to 1.0 (got {total:.4f}). "
            f"Current weights: transparency={weights['transparency']}, "
            f"fee={weights['fee']}, trust={weights['trust']}, "
            f"performance={weights['performance']}."
        )

    return errors


# -- JSON serialization helpers for list/dict fields -------------------------


def _serialize_list_field(value: Any) -> str | None:
    """Serialize a list or None to a JSON string for storage."""
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, str):
        return value  # already serialized
    return json.dumps(value)


def _serialize_dict_field(value: Any) -> str | None:
    """Serialize a dict or None to a JSON string for storage."""
    if value is None:
        return None
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, str):
        return value  # already serialized
    return json.dumps(value)


# -- Formatting helpers ------------------------------------------------------


def _format_deal_template(tmpl: dict[str, Any]) -> str:
    """Format a deal template as a human-readable string."""
    lines = [
        f"Deal Template: {tmpl.get('name', '(unnamed)')}",
        "=" * 50,
        f"  ID: {tmpl.get('id')}",
        f"  Name: {tmpl.get('name')}",
    ]

    if tmpl.get("deal_type_pref"):
        lines.append(f"  Deal Type Preference: {tmpl['deal_type_pref']}")

    if tmpl.get("advertiser_id"):
        lines.append(f"  Advertiser ID: {tmpl['advertiser_id']}")
    else:
        lines.append("  Scope: Agency-wide")

    if tmpl.get("agency_id"):
        lines.append(f"  Agency ID: {tmpl['agency_id']}")

    if tmpl.get("default_price") is not None:
        lines.append(f"  Default Price: ${tmpl['default_price']:.2f}")

    if tmpl.get("max_cpm") is not None:
        lines.append(f"  Max CPM: ${tmpl['max_cpm']:.2f}")

    if tmpl.get("min_impressions") is not None:
        lines.append(f"  Min Impressions: {tmpl['min_impressions']:,}")

    if tmpl.get("default_flight_days") is not None:
        lines.append(f"  Default Flight Days: {tmpl['default_flight_days']}")

    # JSON array fields
    for field_name, label in [
        ("inventory_types", "Inventory Types"),
        ("preferred_publishers", "Preferred Publishers"),
        ("excluded_publishers", "Excluded Publishers"),
    ]:
        val = tmpl.get(field_name)
        if val:
            try:
                parsed = json.loads(val) if isinstance(val, str) else val
                lines.append(f"  {label}: {', '.join(str(x) for x in parsed)}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"  {label}: {val}")

    # JSON object fields
    for field_name, label in [
        ("targeting_defaults", "Targeting Defaults"),
        ("supply_path_prefs", "Supply Path Preferences"),
    ]:
        val = tmpl.get(field_name)
        if val:
            try:
                parsed = json.loads(val) if isinstance(val, str) else val
                lines.append(f"  {label}: {json.dumps(parsed, indent=4)}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"  {label}: {val}")

    if tmpl.get("created_at"):
        lines.append(f"  Created: {tmpl['created_at']}")
    if tmpl.get("updated_at"):
        lines.append(f"  Updated: {tmpl['updated_at']}")

    return "\n".join(lines)


def _format_supply_path_template(tmpl: dict[str, Any]) -> str:
    """Format a supply path template as a human-readable string."""
    lines = [
        f"Supply Path Template: {tmpl.get('name', '(unnamed)')}",
        "=" * 50,
        f"  ID: {tmpl.get('id')}",
        f"  Name: {tmpl.get('name')}",
    ]

    # Scoring weights
    weights_raw = tmpl.get("scoring_weights")
    if weights_raw:
        try:
            weights = json.loads(weights_raw) if isinstance(weights_raw, str) else weights_raw
            lines.append("  Scoring Weights:")
            for key in ("transparency", "fee", "trust", "performance"):
                if key in weights:
                    lines.append(f"    {key}: {weights[key]:.2f}")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"  Scoring Weights: {weights_raw}")

    if tmpl.get("max_reseller_hops") is not None:
        lines.append(f"  Max Reseller Hops: {tmpl['max_reseller_hops']}")

    if tmpl.get("require_sellers_json") is not None:
        lines.append(
            f"  Require sellers.json: {'Yes' if tmpl['require_sellers_json'] else 'No'}"
        )

    # JSON array fields
    for field_name, label in [
        ("preferred_ssps", "Preferred SSPs"),
        ("blocked_ssps", "Blocked SSPs"),
        ("preferred_curators", "Preferred Curators"),
    ]:
        val = tmpl.get(field_name)
        if val:
            try:
                parsed = json.loads(val) if isinstance(val, str) else val
                lines.append(f"  {label}: {', '.join(str(x) for x in parsed)}")
            except (json.JSONDecodeError, TypeError):
                lines.append(f"  {label}: {val}")

    # Rules
    rules_raw = tmpl.get("rules")
    if rules_raw:
        try:
            rules = json.loads(rules_raw) if isinstance(rules_raw, str) else rules_raw
            lines.append(f"  Rules: {json.dumps(rules)}")
        except (json.JSONDecodeError, TypeError):
            lines.append(f"  Rules: {rules_raw}")

    if tmpl.get("created_at"):
        lines.append(f"  Created: {tmpl['created_at']}")
    if tmpl.get("updated_at"):
        lines.append(f"  Updated: {tmpl['updated_at']}")

    return "\n".join(lines)


# -- ManageDealTemplateTool --------------------------------------------------


class ManageDealTemplateTool(BaseTool):
    """CRUD operations for deal templates.

    Deal templates encode an agency's preferred terms for common deal
    types, enabling fast deal duplication and gap-filling.  Templates
    may be agency-wide (advertiser_id=null) or scoped to a specific
    advertiser.

    Actions: create, read, list, update, delete.
    """

    name: str = "manage_deal_template"
    description: str = (
        "Manage deal templates (reusable deal configurations). "
        "Supports create, read, list, update, and delete actions. "
        "Templates encode preferred terms like deal type, max CPM, "
        "inventory types, and publisher preferences. "
        "Can be agency-wide or scoped to a specific advertiser."
    )
    args_schema: type[BaseModel] = ManageDealTemplateInput
    deal_store: Any = Field(exclude=True)

    def _run(self, action: str, params_json: str) -> str:
        """Execute a CRUD action on deal templates.

        Args:
            action: One of 'create', 'read', 'list', 'update', 'delete'.
            params_json: JSON string with action-specific parameters.

        Returns:
            Human-readable result string.
        """
        if action not in VALID_ACTIONS:
            return (
                f"Error: Invalid action '{action}'. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}."
            )

        try:
            params = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        if action == "create":
            return self._create(params)
        elif action == "read":
            return self._read(params)
        elif action == "list":
            return self._list(params)
        elif action == "update":
            return self._update(params)
        elif action == "delete":
            return self._delete(params)

        return f"Error: Unhandled action '{action}'."

    def _create(self, params: dict[str, Any]) -> str:
        """Create a new deal template."""
        name = params.get("name")
        if not name or not str(name).strip():
            return "Error: 'name' is required for creating a deal template."

        try:
            template_id = self.deal_store.save_deal_template(
                name=name,
                deal_type_pref=params.get("deal_type_pref"),
                inventory_types=_serialize_list_field(params.get("inventory_types")),
                preferred_publishers=_serialize_list_field(
                    params.get("preferred_publishers")
                ),
                excluded_publishers=_serialize_list_field(
                    params.get("excluded_publishers")
                ),
                targeting_defaults=_serialize_dict_field(
                    params.get("targeting_defaults")
                ),
                default_price=params.get("default_price"),
                max_cpm=params.get("max_cpm"),
                min_impressions=params.get("min_impressions"),
                default_flight_days=params.get("default_flight_days"),
                supply_path_prefs=_serialize_dict_field(
                    params.get("supply_path_prefs")
                ),
                advertiser_id=params.get("advertiser_id"),
                agency_id=params.get("agency_id"),
            )
        except Exception as exc:
            return f"Error creating deal template: {exc}"

        return (
            f"Deal template created successfully.\n"
            f"  ID: {template_id}\n"
            f"  Name: {name}"
        )

    def _read(self, params: dict[str, Any]) -> str:
        """Read a deal template by ID."""
        template_id = params.get("template_id")
        if not template_id:
            return "Error: 'template_id' is required for reading a deal template."

        tmpl = self.deal_store.get_deal_template(template_id)
        if tmpl is None:
            return f"Deal template not found: {template_id}"

        return _format_deal_template(tmpl)

    def _list(self, params: dict[str, Any]) -> str:
        """List deal templates with optional filters."""
        kwargs: dict[str, Any] = {}
        if params.get("advertiser_id"):
            kwargs["advertiser_id"] = params["advertiser_id"]
        if params.get("deal_type_pref"):
            kwargs["deal_type_pref"] = params["deal_type_pref"]

        templates = self.deal_store.list_deal_templates(**kwargs)

        if not templates:
            return "No deal templates found."

        lines = [f"Deal Templates: {len(templates)} found"]
        lines.append("")
        for tmpl in templates:
            name = tmpl.get("name", "(unnamed)")
            tid = tmpl.get("id", "?")
            dtype = tmpl.get("deal_type_pref") or "any"
            adv = tmpl.get("advertiser_id") or "agency-wide"
            max_cpm = tmpl.get("max_cpm")
            cpm_str = f"${max_cpm:.2f}" if max_cpm is not None else "N/A"
            lines.append(f"  [{tid}] {name}")
            lines.append(f"    Type: {dtype} | Scope: {adv} | Max CPM: {cpm_str}")
            lines.append("")

        return "\n".join(lines)

    def _update(self, params: dict[str, Any]) -> str:
        """Update a deal template."""
        template_id = params.pop("template_id", None)
        if not template_id:
            return "Error: 'template_id' is required for updating a deal template."

        # Serialize list/dict fields if present
        update_kwargs: dict[str, Any] = {}
        for key, val in params.items():
            if key in ("inventory_types", "preferred_publishers", "excluded_publishers"):
                update_kwargs[key] = _serialize_list_field(val)
            elif key in ("targeting_defaults", "supply_path_prefs"):
                update_kwargs[key] = _serialize_dict_field(val)
            else:
                update_kwargs[key] = val

        result = self.deal_store.update_deal_template(template_id, **update_kwargs)
        if not result:
            return f"Deal template not found: {template_id}"

        return f"Deal template updated successfully.\n  ID: {template_id}"

    def _delete(self, params: dict[str, Any]) -> str:
        """Delete a deal template."""
        template_id = params.get("template_id")
        if not template_id:
            return "Error: 'template_id' is required for deleting a deal template."

        result = self.deal_store.delete_deal_template(template_id)
        if not result:
            return f"Deal template not found: {template_id}"

        return f"Deal template deleted successfully.\n  ID: {template_id}"


# -- ManageSupplyPathTemplateTool --------------------------------------------


class ManageSupplyPathTemplateTool(BaseTool):
    """CRUD operations for supply path optimization templates.

    Supply path templates codify SPO routing preferences -- which
    supply paths the agency prefers and why.  Each template has
    scoring weights (transparency, fee, trust, performance) that
    must sum to 1.0.

    Actions: create, read, list, update, delete.
    """

    name: str = "manage_supply_path_template"
    description: str = (
        "Manage supply path optimization templates. "
        "Supports create, read, list, update, and delete actions. "
        "Templates codify SPO routing preferences with scoring weights "
        "(transparency, fee, trust, performance -- must sum to 1.0), "
        "max reseller hops, preferred/blocked SSPs, and routing rules."
    )
    args_schema: type[BaseModel] = ManageSupplyPathTemplateInput
    deal_store: Any = Field(exclude=True)

    def _run(self, action: str, params_json: str) -> str:
        """Execute a CRUD action on supply path templates.

        Args:
            action: One of 'create', 'read', 'list', 'update', 'delete'.
            params_json: JSON string with action-specific parameters.

        Returns:
            Human-readable result string.
        """
        if action not in VALID_ACTIONS:
            return (
                f"Error: Invalid action '{action}'. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}."
            )

        try:
            params = json.loads(params_json)
        except (json.JSONDecodeError, TypeError) as exc:
            return f"Error: Invalid JSON input -- {exc}"

        if action == "create":
            return self._create(params)
        elif action == "read":
            return self._read(params)
        elif action == "list":
            return self._list(params)
        elif action == "update":
            return self._update(params)
        elif action == "delete":
            return self._delete(params)

        return f"Error: Unhandled action '{action}'."

    def _create(self, params: dict[str, Any]) -> str:
        """Create a new supply path template."""
        name = params.get("name")
        if not name or not str(name).strip():
            return "Error: 'name' is required for creating a supply path template."

        # Validate scoring weights if provided
        scoring_weights = params.get("scoring_weights")
        if scoring_weights:
            if isinstance(scoring_weights, str):
                try:
                    scoring_weights = json.loads(scoring_weights)
                except (json.JSONDecodeError, TypeError):
                    return "Error: scoring_weights must be a valid JSON object."

            weight_errors = _validate_scoring_weights(scoring_weights)
            if weight_errors:
                return "Error: " + " ".join(weight_errors)

        try:
            template_id = self.deal_store.save_supply_path_template(
                name=name,
                scoring_weights=_serialize_dict_field(scoring_weights),
                max_reseller_hops=params.get("max_reseller_hops"),
                require_sellers_json=(
                    1 if params.get("require_sellers_json") else None
                ),
                preferred_ssps=_serialize_list_field(params.get("preferred_ssps")),
                blocked_ssps=_serialize_list_field(params.get("blocked_ssps")),
                preferred_curators=_serialize_list_field(
                    params.get("preferred_curators")
                ),
                rules=_serialize_list_field(params.get("rules")),
            )
        except Exception as exc:
            return f"Error creating supply path template: {exc}"

        return (
            f"Supply path template created successfully.\n"
            f"  ID: {template_id}\n"
            f"  Name: {name}"
        )

    def _read(self, params: dict[str, Any]) -> str:
        """Read a supply path template by ID."""
        template_id = params.get("template_id")
        if not template_id:
            return (
                "Error: 'template_id' is required for reading a "
                "supply path template."
            )

        tmpl = self.deal_store.get_supply_path_template(template_id)
        if tmpl is None:
            return f"Supply path template not found: {template_id}"

        return _format_supply_path_template(tmpl)

    def _list(self, params: dict[str, Any]) -> str:
        """List supply path templates."""
        templates = self.deal_store.list_supply_path_templates()

        if not templates:
            return "No supply path templates found."

        lines = [f"Supply Path Templates: {len(templates)} found"]
        lines.append("")
        for tmpl in templates:
            name = tmpl.get("name", "(unnamed)")
            tid = tmpl.get("id", "?")
            hops = tmpl.get("max_reseller_hops")
            hops_str = str(hops) if hops is not None else "N/A"
            lines.append(f"  [{tid}] {name}")
            lines.append(f"    Max Hops: {hops_str}")

            # Show weights if available
            weights_raw = tmpl.get("scoring_weights")
            if weights_raw:
                try:
                    weights = (
                        json.loads(weights_raw)
                        if isinstance(weights_raw, str)
                        else weights_raw
                    )
                    weight_parts = [
                        f"{k}={weights.get(k, 0):.1f}"
                        for k in ("transparency", "fee", "trust", "performance")
                    ]
                    lines.append(f"    Weights: {', '.join(weight_parts)}")
                except (json.JSONDecodeError, TypeError):
                    pass

            lines.append("")

        return "\n".join(lines)

    def _update(self, params: dict[str, Any]) -> str:
        """Update a supply path template."""
        template_id = params.pop("template_id", None)
        if not template_id:
            return (
                "Error: 'template_id' is required for updating a "
                "supply path template."
            )

        # Validate scoring weights if being updated
        scoring_weights = params.get("scoring_weights")
        if scoring_weights is not None:
            if isinstance(scoring_weights, str):
                try:
                    scoring_weights = json.loads(scoring_weights)
                except (json.JSONDecodeError, TypeError):
                    return "Error: scoring_weights must be a valid JSON object."

            weight_errors = _validate_scoring_weights(scoring_weights)
            if weight_errors:
                return "Error: " + " ".join(weight_errors)
            params["scoring_weights"] = _serialize_dict_field(scoring_weights)

        # Serialize list fields if present
        update_kwargs: dict[str, Any] = {}
        for key, val in params.items():
            if key in ("preferred_ssps", "blocked_ssps", "preferred_curators", "rules"):
                update_kwargs[key] = _serialize_list_field(val)
            elif key == "require_sellers_json":
                update_kwargs[key] = 1 if val else 0
            else:
                update_kwargs[key] = val

        result = self.deal_store.update_supply_path_template(
            template_id, **update_kwargs
        )
        if not result:
            return f"Supply path template not found: {template_id}"

        return f"Supply path template updated successfully.\n  ID: {template_id}"

    def _delete(self, params: dict[str, Any]) -> str:
        """Delete a supply path template."""
        template_id = params.get("template_id")
        if not template_id:
            return (
                "Error: 'template_id' is required for deleting a "
                "supply path template."
            )

        result = self.deal_store.delete_supply_path_template(template_id)
        if not result:
            return f"Supply path template not found: {template_id}"

        return f"Supply path template deleted successfully.\n  ID: {template_id}"
