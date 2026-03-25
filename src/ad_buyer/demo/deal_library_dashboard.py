# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""DealLibrary Phase 1 demo dashboard.

Standalone Flask app that exercises all Phase 1 capabilities:
  - Schema status inspection
  - Deal portfolio listing / filtering / search
  - CSV deal import
  - Manual deal entry
  - Event log viewing
  - Agent info display

Run with:
    cd ad_buyer_system && source venv/bin/activate
    python -m ad_buyer.demo.deal_library_dashboard
    # Opens on http://localhost:5050
"""

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ..storage.deal_store import DealStore
from ..storage.schema import SCHEMA_VERSION
from ..tools.deal_import import parse_csv_deals
from ..tools.deal_library.deal_entry import (
    VALID_DEAL_TYPES,
    VALID_MEDIA_TYPES,
    VALID_PRICE_MODELS,
    VALID_SELLER_TYPES,
    ManualDealEntry,
    create_manual_deal,
)
from .seed_data import seed_demo_data

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def create_app(database_url: str = "sqlite:///:memory:") -> Flask:
    """Create and configure the dashboard Flask app.

    Args:
        database_url: SQLite connection string for the DealStore.

    Returns:
        Configured Flask application.
    """
    app = Flask(
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )
    app.config["DATABASE_URL"] = database_url

    # Initialize store
    store = DealStore(database_url)
    store.connect()
    app.config["DEAL_STORE"] = store

    # Seed data on startup (only if the DB is empty)
    existing = store.list_deals(limit=1)
    if not existing:
        seed_demo_data(store)
        logger.info("Seeded demo data into the dashboard database")

    # Register routes
    _register_routes(app, store)

    return app


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def _register_routes(app: Flask, store: DealStore) -> None:
    """Register all routes on the app."""

    # -- Page ---------------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    # -- API: Schema --------------------------------------------------------

    @app.route("/api/schema")
    def api_schema():
        """Return schema version and table info."""
        conn = store._conn
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = []
        for row in cursor.fetchall():
            name = row["name"] if isinstance(row, dict) else row[0]
            count_cursor = conn.execute(f"SELECT COUNT(*) FROM [{name}]")
            count = count_cursor.fetchone()[0]
            tables.append({"name": name, "row_count": count})

        # v2 columns on deals table
        col_cursor = conn.execute("PRAGMA table_info(deals)")
        all_cols = [r[1] if not isinstance(r, dict) else r["name"] for r in col_cursor.fetchall()]
        v2_cols = [c for c in all_cols if c in store._V2_DEAL_COLUMNS]

        return jsonify(
            {
                "schema_version": SCHEMA_VERSION,
                "tables": tables,
                "v2_columns": v2_cols,
            }
        )

    # -- API: Deals ---------------------------------------------------------

    @app.route("/api/deals")
    def api_deals():
        """List deals with optional query-param filters."""
        filters: dict[str, Any] = {}
        for key in ("status", "media_type", "deal_type", "seller_domain"):
            val = request.args.get(key)
            if val:
                filters[key] = val

        limit = request.args.get("limit", 200, type=int)
        deals = store.list_deals(limit=limit, **filters)
        return jsonify({"deals": deals, "count": len(deals)})

    @app.route("/api/deals/<deal_id>")
    def api_deal_detail(deal_id: str):
        """Return full deal detail including metadata, activations, perf."""
        deal = store.get_deal(deal_id)
        if deal is None:
            return jsonify({"error": "Deal not found"}), 404

        metadata = store.get_portfolio_metadata(deal_id)
        activations = store.get_deal_activations(deal_id)
        perf = store.get_performance_cache(deal_id)

        return jsonify(
            {
                "deal": deal,
                "portfolio_metadata": metadata,
                "activations": activations,
                "performance_cache": perf,
            }
        )

    # -- API: Create deal (manual entry) ------------------------------------

    @app.route("/api/deals", methods=["POST"])
    def api_create_deal():
        """Create a deal from the manual entry form."""
        data = request.get_json(silent=True) or {}

        # Build ManualDealEntry from posted data
        try:
            entry = ManualDealEntry(**data)
        except (ValueError, TypeError) as exc:
            return jsonify({"success": False, "errors": [str(exc)]}), 400

        result = create_manual_deal(entry)
        if not result.success:
            return jsonify({"success": False, "errors": result.errors}), 400

        # Persist via DealStore.
        # _build_deal_data() includes v2 fields as top-level keys,
        # so deal_data can be passed directly to save_deal().
        deal_data = result.deal_data
        deal_id = store.save_deal(**deal_data)

        # Save portfolio metadata
        if result.metadata:
            tags_val = result.metadata.get("tags")
            if isinstance(tags_val, list):
                tags_val = json.dumps(tags_val)
            store.save_portfolio_metadata(
                deal_id=deal_id,
                import_source=result.metadata.get("import_source", "MANUAL"),
                import_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                tags=tags_val,
                advertiser_id=result.metadata.get("advertiser_id"),
            )

        # Emit event
        store.save_event(
            event_type="deal.imported",
            deal_id=deal_id,
            payload=json.dumps(
                {
                    "import_source": "MANUAL",
                    "display_name": data.get("display_name", ""),
                }
            ),
        )

        return jsonify({"success": True, "deal_id": deal_id})

    # -- API: CSV Import ----------------------------------------------------

    @app.route("/api/import", methods=["POST"])
    def api_import_csv():
        """Parse an uploaded CSV file and return results (without saving)."""
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        uploaded = request.files["file"]
        if not uploaded.filename:
            return jsonify({"error": "Empty filename"}), 400

        # Save to a temp file for parse_csv_deals
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as tmp:
            uploaded.save(tmp)
            tmp_path = tmp.name

        try:
            result = parse_csv_deals(tmp_path)
        finally:
            os.unlink(tmp_path)

        errors_list = [
            {
                "row_number": e.row_number,
                "field": e.field,
                "value": e.value,
                "message": e.message,
            }
            for e in result.errors
        ]

        return jsonify(
            {
                "total_rows": result.total_rows,
                "successful": result.successful,
                "failed": result.failed,
                "skipped": result.skipped,
                "deals": result.deals,
                "errors": errors_list,
            }
        )

    @app.route("/api/import/save", methods=["POST"])
    def api_import_save():
        """Save previously parsed deals to DealStore."""
        data = request.get_json(silent=True) or {}
        deals = data.get("deals", [])
        if not deals:
            return jsonify({"error": "No deals to save"}), 400

        saved_ids = []
        save_errors = []
        for i, deal_dict in enumerate(deals):
            try:
                # Ensure required fields for save_deal
                seller_url = deal_dict.pop("seller_url", "")
                product_id = deal_dict.pop("product_id", "imported")
                display_name = deal_dict.get("display_name") or deal_dict.pop(
                    "product_name", "Imported Deal"
                )

                deal_id = store.save_deal(
                    seller_url=seller_url or "https://imported.example.com",
                    product_id=product_id,
                    product_name=display_name,
                    display_name=display_name,
                    deal_type=deal_dict.get("deal_type", "PD"),
                    status=deal_dict.get("status", "draft"),
                    seller_deal_id=deal_dict.get("seller_deal_id"),
                    seller_org=deal_dict.get("seller_org"),
                    seller_domain=deal_dict.get("seller_domain"),
                    media_type=deal_dict.get("media_type"),
                    price=deal_dict.get("fixed_price_cpm") or deal_dict.get("bid_floor_cpm"),
                    fixed_price_cpm=deal_dict.get("fixed_price_cpm"),
                    bid_floor_cpm=deal_dict.get("bid_floor_cpm"),
                    impressions=deal_dict.get("impressions"),
                    flight_start=deal_dict.get("flight_start"),
                    flight_end=deal_dict.get("flight_end"),
                    currency=deal_dict.get("currency", "USD"),
                    description=deal_dict.get("description"),
                    buyer_org=deal_dict.get("buyer_org"),
                )

                # Save metadata
                store.save_portfolio_metadata(
                    deal_id=deal_id,
                    import_source="CSV",
                    import_date=datetime.now(UTC).strftime("%Y-%m-%d"),
                )

                # Emit event
                store.save_event(
                    event_type="deal.imported",
                    deal_id=deal_id,
                    payload=json.dumps(
                        {
                            "import_source": "CSV",
                            "display_name": display_name,
                        }
                    ),
                )

                saved_ids.append(deal_id)
            except (OSError, ValueError, TypeError) as exc:
                save_errors.append({"index": i, "error": str(exc)})

        return jsonify(
            {
                "saved": len(saved_ids),
                "errors": save_errors,
                "deal_ids": saved_ids,
            }
        )

    # -- API: Search --------------------------------------------------------

    @app.route("/api/search")
    def api_search():
        """Search deals by free-text query."""
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"error": "Empty search query"}), 400

        query_lower = query.lower()
        deals = store.list_deals(limit=10000)
        search_fields = [
            "display_name",
            "product_name",
            "description",
            "seller_org",
            "seller_domain",
        ]

        matches = []
        for deal in deals:
            for field in search_fields:
                val = deal.get(field)
                if val and query_lower in str(val).lower():
                    matches.append(deal)
                    break

        return jsonify({"query": query, "results": matches, "count": len(matches)})

    # -- API: Summary -------------------------------------------------------

    @app.route("/api/summary")
    def api_summary():
        """Return portfolio aggregate statistics."""
        deals = store.list_deals(limit=10000)
        total = len(deals)

        status_counts: dict[str, int] = {}
        media_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        seller_counts: dict[str, int] = {}
        total_value = 0.0
        total_impressions = 0

        for deal in deals:
            s = deal.get("status", "unknown")
            status_counts[s] = status_counts.get(s, 0) + 1

            mt = deal.get("media_type") or "N/A"
            media_counts[mt] = media_counts.get(mt, 0) + 1

            dt = deal.get("deal_type", "unknown")
            type_counts[dt] = type_counts.get(dt, 0) + 1

            seller = deal.get("seller_org") or deal.get("seller_domain") or "Unknown"
            seller_counts[seller] = seller_counts.get(seller, 0) + 1

            price = deal.get("price")
            imps = deal.get("impressions")
            if price is not None and imps is not None:
                total_value += price * imps / 1000.0
            if imps is not None:
                total_impressions += imps

        top_sellers = sorted(seller_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return jsonify(
            {
                "total_deals": total,
                "total_value": round(total_value, 2),
                "total_impressions": total_impressions,
                "by_status": status_counts,
                "by_media_type": media_counts,
                "by_deal_type": type_counts,
                "top_sellers": [{"seller": s, "count": c} for s, c in top_sellers],
            }
        )

    # -- API: Events --------------------------------------------------------

    @app.route("/api/events")
    def api_events():
        """Return recent Phase 1 events."""
        phase1_types = [
            "deal.imported",
            "deal.template_created",
            "portfolio.inspected",
            "deal.manual_action_required",
        ]
        limit = request.args.get("limit", 50, type=int)

        # Fetch all recent events and filter client-side for Phase 1 types
        all_events = store.list_events(limit=limit * 4)
        events = [e for e in all_events if e.get("event_type") in phase1_types][:limit]

        return jsonify({"events": events, "count": len(events)})

    # -- API: Agent info ----------------------------------------------------

    @app.route("/api/agent-info")
    def api_agent_info():
        """Return DealLibrary agent configuration (static, no instantiation)."""
        # Read from the module docstring and create_deal_library_agent

        # Extract the Agent kwargs without actually creating the agent
        # (which would require LLM config). Instead, read the source.
        info = {
            "role": "Deal Library - Portfolio Manager",
            "goal": (
                "Manage deal portfolios -- import, catalog, inspect, organize, "
                "migrate, and optimize deals across publishers, SSPs, and DSPs. "
                "Treat deals as a managed asset class, ensuring the agency's deal "
                "inventory is current, well-organized, and aligned with campaign needs."
            ),
            "backstory_summary": (
                "Portfolio management specialist with deep expertise in "
                "programmatic deal operations across the ad tech ecosystem. "
                "Capabilities: deal portfolio organization, CSV/bulk import, "
                "template creation, supply path analysis, cross-platform tracking "
                "(TTD, DV360, Xandr, Amazon DSP), migration workflows, price "
                "comparison, and gap analysis."
            ),
            "l1_routing": {
                "deal_library_keywords": [
                    "portfolio",
                    "existing deals",
                    "my deals",
                    "migrate",
                    "clone",
                    "deprecate",
                    "compare prices",
                    "import",
                    "catalog",
                    "gap analysis",
                    "sunset",
                ],
                "campaign_flow_keywords": [
                    "campaign",
                    "book for campaign",
                    "budget",
                    "target audience",
                    "pacing",
                    "flight dates",
                    "launch",
                ],
                "ambiguous_response": (
                    "Are you looking to manage your existing deal portfolio, "
                    "or book deals for a specific campaign?"
                ),
            },
            "phase1_tools": [
                "list_portfolio",
                "search_portfolio",
                "portfolio_summary",
                "inspect_deal",
                "manual_deal_entry",
                "csv_deal_import",
            ],
            "phase1_event_types": [
                "deal.imported",
                "deal.template_created",
                "portfolio.inspected",
                "deal.manual_action_required",
            ],
        }
        return jsonify(info)

    # -- API: Enum values (for form dropdowns) ------------------------------

    @app.route("/api/enums")
    def api_enums():
        """Return valid enum values for form dropdowns."""
        return jsonify(
            {
                "deal_types": sorted(VALID_DEAL_TYPES),
                "media_types": sorted(VALID_MEDIA_TYPES),
                "price_models": sorted(VALID_PRICE_MODELS),
                "seller_types": sorted(VALID_SELLER_TYPES),
                "statuses": ["draft", "active", "paused"],
            }
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the dashboard development server."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    db_path = os.environ.get("DASHBOARD_DB", "sqlite:///deal_library_demo.db")
    app = create_app(database_url=db_path)

    port = int(os.environ.get("DASHBOARD_PORT", "5050"))
    print(f"\n  Deal Library Dashboard running at http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
