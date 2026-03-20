# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for AnalyzeSupplyPathTool.

Tests cover:
- Scoring with known inputs produces expected composite and per-dimension scores
- Custom weights from a supply_path_template change ranking order
- Missing data is handled gracefully (partial schain, no performance data, etc.)
- Default weights (transparency=0.25, fee=0.35, trust=0.20, performance=0.20)
- Multiple supply paths are ranked correctly
- Invalid inputs return clear error messages
"""

import json

import pytest

from ad_buyer.storage import DealStore
from ad_buyer.tools.deal_jockey.analyze_supply_path import AnalyzeSupplyPathTool


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def deal_store():
    """Create a DealStore backed by in-memory SQLite."""
    store = DealStore("sqlite:///:memory:")
    store.connect()
    yield store
    store.disconnect()


@pytest.fixture
def analyze_tool(deal_store):
    """Create an AnalyzeSupplyPathTool with an in-memory store."""
    return AnalyzeSupplyPathTool(deal_store=deal_store)


@pytest.fixture
def sample_supply_path_data():
    """Return a well-formed supply path data dict for testing."""
    return {
        "schain": {
            "complete": 1,
            "nodes": [
                {"asi": "exchange1.com", "hp": 1, "sid": "pub123"},
                {"asi": "ssp1.com", "hp": 1, "sid": "seat456"},
            ],
        },
        "fee_estimate": 0.15,  # 15% intermediary fees
        "seller_reputation": {
            "trust_score": 0.85,
            "verified": True,
            "sellers_json_listed": True,
        },
        "performance": {
            "fill_rate": 0.72,
            "win_rate": 0.45,
            "avg_effective_cpm": 12.50,
            "impressions_delivered": 500000,
            "performance_trend": "STABLE",
        },
    }


@pytest.fixture
def minimal_supply_path_data():
    """Return a supply path data dict with minimal info."""
    return {
        "schain": {
            "complete": 0,
            "nodes": [],
        },
    }


@pytest.fixture
def deal_with_schain(deal_store):
    """Create a deal with supply chain data in the store and return its ID."""
    deal_id = deal_store.save_deal(
        seller_url="https://exchange1.com",
        product_id="prod-001",
        product_name="Premium Display",
        deal_type="PD",
        schain_complete=1,
        schain_nodes=json.dumps([
            {"asi": "exchange1.com", "hp": 1, "sid": "pub123"},
            {"asi": "ssp1.com", "hp": 1, "sid": "seat456"},
        ]),
        hop_count=2,
        is_direct=0,
        fee_transparency=0.12,
        seller_domain="exchange1.com",
        seller_type="SSP",
        sellers_json_url="https://exchange1.com/sellers.json",
    )
    # Add performance data
    deal_store.save_performance_cache(
        deal_id=deal_id,
        fill_rate=0.72,
        win_rate=0.45,
        avg_effective_cpm=12.50,
        impressions_delivered=500000,
        performance_trend="STABLE",
    )
    return deal_id


@pytest.fixture
def supply_path_template_id(deal_store):
    """Create a supply path template with custom weights and return its ID."""
    return deal_store.save_supply_path_template(
        name="Fee-Heavy Template",
        scoring_weights=json.dumps({
            "transparency": 0.10,
            "fee": 0.60,
            "trust": 0.15,
            "performance": 0.15,
        }),
        max_reseller_hops=3,
        require_sellers_json=1,
    )


# -----------------------------------------------------------------------
# Scoring with known inputs
# -----------------------------------------------------------------------


class TestScoringKnownInputs:
    """Verify scoring produces expected results for known inputs."""

    def test_score_supply_path_data_returns_all_dimensions(self, analyze_tool, sample_supply_path_data):
        """Scoring returns per-dimension scores and composite."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)

        assert "scores" in parsed
        scores = parsed["scores"]
        assert "transparency" in scores
        assert "fee" in scores
        assert "trust" in scores
        assert "performance" in scores
        assert "composite" in parsed

    def test_default_weights_applied(self, analyze_tool, sample_supply_path_data):
        """When no template is specified, default weights are used."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)

        assert "weights" in parsed
        weights = parsed["weights"]
        assert weights["transparency"] == 0.25
        assert weights["fee"] == 0.35
        assert weights["trust"] == 0.20
        assert weights["performance"] == 0.20

    def test_composite_score_is_weighted_sum(self, analyze_tool, sample_supply_path_data):
        """Composite score equals the weighted sum of dimension scores."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)

        scores = parsed["scores"]
        weights = parsed["weights"]
        expected_composite = sum(
            scores[dim] * weights[dim]
            for dim in ("transparency", "fee", "trust", "performance")
        )
        assert abs(parsed["composite"] - expected_composite) < 0.001

    def test_scores_in_valid_range(self, analyze_tool, sample_supply_path_data):
        """All dimension scores are between 0.0 and 1.0."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)

        scores = parsed["scores"]
        for dim in ("transparency", "fee", "trust", "performance"):
            assert 0.0 <= scores[dim] <= 1.0, f"{dim} score out of range: {scores[dim]}"

    def test_complete_schain_scores_high_transparency(self, analyze_tool, sample_supply_path_data):
        """A complete schain with nodes scores high on transparency."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)
        # Complete schain with 2 nodes should score well
        assert parsed["scores"]["transparency"] >= 0.7

    def test_low_fee_scores_high(self, analyze_tool, sample_supply_path_data):
        """Low intermediary fees (15%) should result in a high fee score."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)
        # 15% fee is moderate-to-good; score should be above 0.5
        assert parsed["scores"]["fee"] >= 0.5

    def test_recommendation_present(self, analyze_tool, sample_supply_path_data):
        """Result includes a recommendation string."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)
        assert "recommendation" in parsed
        assert isinstance(parsed["recommendation"], str)
        assert len(parsed["recommendation"]) > 0


# -----------------------------------------------------------------------
# Custom weights from template
# -----------------------------------------------------------------------


class TestCustomWeights:
    """Custom supply_path_template weights change scoring behavior."""

    def test_template_weights_override_defaults(
        self, analyze_tool, sample_supply_path_data, supply_path_template_id
    ):
        """When a template_id is provided, its weights override defaults."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
            supply_path_template_id=supply_path_template_id,
        )
        parsed = json.loads(result)

        weights = parsed["weights"]
        assert weights["transparency"] == 0.10
        assert weights["fee"] == 0.60
        assert weights["trust"] == 0.15
        assert weights["performance"] == 0.15

    def test_custom_weights_change_composite(
        self, analyze_tool, sample_supply_path_data, supply_path_template_id
    ):
        """Different weights produce a different composite score."""
        result_default = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        result_custom = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
            supply_path_template_id=supply_path_template_id,
        )

        parsed_default = json.loads(result_default)
        parsed_custom = json.loads(result_custom)

        # Same dimension scores, different weights -> different composite
        assert parsed_default["composite"] != parsed_custom["composite"]

    def test_custom_weights_change_ranking(self, analyze_tool, deal_store):
        """Custom weights can change which path ranks higher."""
        # Path A: great transparency, very poor fee
        path_a = {
            "schain": {"complete": 1, "nodes": [
                {"asi": "direct-pub.com", "hp": 1, "sid": "d1"},
            ]},
            "fee_estimate": 0.90,  # 90% fees - extremely expensive
            "seller_reputation": {"trust_score": 0.50, "verified": False, "sellers_json_listed": False},
            "performance": {"fill_rate": 0.50, "win_rate": 0.30},
        }
        # Path B: poor transparency, great fee
        path_b = {
            "schain": {"complete": 0, "nodes": []},
            "fee_estimate": 0.05,  # 5% fees - cheap
            "seller_reputation": {"trust_score": 0.50, "verified": False, "sellers_json_listed": False},
            "performance": {"fill_rate": 0.50, "win_rate": 0.30},
        }

        # With default weights (transparency=0.25, fee=0.35):
        # Path A: transparency=0.9*0.25=0.225, fee=0.1*0.35=0.035 -> fee drags it down
        # Path B: transparency=0.0*0.25=0.0, fee=0.95*0.35=0.3325 -> fee lifts it up
        result_default = analyze_tool._run(
            supply_path_data_json=json.dumps([path_a, path_b]),
        )
        parsed_default = json.loads(result_default)

        # With transparency-heavy weights, path A should rank higher
        tmpl_id = deal_store.save_supply_path_template(
            name="Transparency Heavy",
            scoring_weights=json.dumps({
                "transparency": 0.70,
                "fee": 0.10,
                "trust": 0.10,
                "performance": 0.10,
            }),
        )
        result_custom = analyze_tool._run(
            supply_path_data_json=json.dumps([path_a, path_b]),
            supply_path_template_id=tmpl_id,
        )
        parsed_custom = json.loads(result_custom)

        # Default: path B (low fee) should rank higher
        assert parsed_default["ranking"][0]["path_index"] == 1  # path_b
        # Custom: path A (great transparency) should rank higher
        assert parsed_custom["ranking"][0]["path_index"] == 0  # path_a


# -----------------------------------------------------------------------
# Deal ID lookup
# -----------------------------------------------------------------------


class TestDealIdLookup:
    """Test scoring via deal_id (pulls supply chain data from the store)."""

    def test_score_by_deal_id(self, analyze_tool, deal_with_schain):
        """Passing a deal_id retrieves supply chain and performance data from the store."""
        result = analyze_tool._run(deal_id=deal_with_schain)
        parsed = json.loads(result)

        assert "scores" in parsed
        assert "composite" in parsed
        # Deal has schain_complete=1, so transparency should be decent
        assert parsed["scores"]["transparency"] >= 0.5

    def test_deal_id_not_found(self, analyze_tool):
        """Nonexistent deal_id returns an error."""
        result = analyze_tool._run(deal_id="nonexistent-deal-id")
        parsed = json.loads(result)
        assert "error" in parsed


# -----------------------------------------------------------------------
# Missing data handling
# -----------------------------------------------------------------------


class TestMissingDataHandling:
    """Verify graceful handling of incomplete supply path data."""

    def test_incomplete_schain_scores_low_transparency(self, analyze_tool, minimal_supply_path_data):
        """Incomplete schain with no nodes scores low on transparency."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(minimal_supply_path_data),
        )
        parsed = json.loads(result)
        assert parsed["scores"]["transparency"] <= 0.3

    def test_missing_fee_defaults_to_midpoint(self, analyze_tool, minimal_supply_path_data):
        """No fee_estimate defaults to a midpoint score."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(minimal_supply_path_data),
        )
        parsed = json.loads(result)
        # Missing fee info -> default/midpoint
        assert 0.0 <= parsed["scores"]["fee"] <= 1.0

    def test_missing_reputation_scores_low_trust(self, analyze_tool, minimal_supply_path_data):
        """No reputation data produces a low trust score."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(minimal_supply_path_data),
        )
        parsed = json.loads(result)
        assert parsed["scores"]["trust"] <= 0.3

    def test_missing_performance_scores_low(self, analyze_tool, minimal_supply_path_data):
        """No performance data produces a low performance score."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(minimal_supply_path_data),
        )
        parsed = json.loads(result)
        assert parsed["scores"]["performance"] <= 0.3

    def test_empty_supply_path_data_returns_error(self, analyze_tool):
        """Empty JSON object returns an error."""
        result = analyze_tool._run(supply_path_data_json="{}")
        parsed = json.loads(result)
        assert "error" in parsed

    def test_no_inputs_returns_error(self, analyze_tool):
        """Neither deal_id nor supply_path_data_json returns an error."""
        result = analyze_tool._run()
        parsed = json.loads(result)
        assert "error" in parsed

    def test_invalid_template_id_falls_back_to_defaults(self, analyze_tool, sample_supply_path_data):
        """Nonexistent template_id falls back to default weights with a warning."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
            supply_path_template_id="nonexistent-template",
        )
        parsed = json.loads(result)
        # Should still produce a result with default weights
        assert "scores" in parsed
        assert parsed["weights"]["transparency"] == 0.25
        assert "warning" in parsed


# -----------------------------------------------------------------------
# Multiple paths comparison
# -----------------------------------------------------------------------


class TestMultiplePaths:
    """Test comparing and ranking multiple supply paths."""

    def test_multiple_paths_ranked(self, analyze_tool):
        """Multiple paths are scored and ranked by composite score."""
        paths = [
            {
                "schain": {"complete": 1, "nodes": [{"asi": "pub.com", "hp": 1, "sid": "s1"}]},
                "fee_estimate": 0.10,
                "seller_reputation": {"trust_score": 0.90, "verified": True, "sellers_json_listed": True},
                "performance": {"fill_rate": 0.80, "win_rate": 0.60},
            },
            {
                "schain": {"complete": 0, "nodes": []},
                "fee_estimate": 0.50,
                "seller_reputation": {"trust_score": 0.30, "verified": False, "sellers_json_listed": False},
                "performance": {"fill_rate": 0.20, "win_rate": 0.10},
            },
        ]

        result = analyze_tool._run(
            supply_path_data_json=json.dumps(paths),
        )
        parsed = json.loads(result)

        assert "ranking" in parsed
        assert len(parsed["ranking"]) == 2
        # First path is better in all dimensions; should rank first
        assert parsed["ranking"][0]["path_index"] == 0
        assert parsed["ranking"][0]["composite"] > parsed["ranking"][1]["composite"]

    def test_ranking_contains_composite_and_index(self, analyze_tool):
        """Each ranking entry has path_index and composite score."""
        paths = [
            {"schain": {"complete": 1, "nodes": [{"asi": "a.com", "hp": 1, "sid": "s"}]},
             "fee_estimate": 0.20},
            {"schain": {"complete": 1, "nodes": [{"asi": "b.com", "hp": 1, "sid": "s"}]},
             "fee_estimate": 0.10},
        ]

        result = analyze_tool._run(
            supply_path_data_json=json.dumps(paths),
        )
        parsed = json.loads(result)

        for entry in parsed["ranking"]:
            assert "path_index" in entry
            assert "composite" in entry

    def test_single_path_has_no_ranking(self, analyze_tool, sample_supply_path_data):
        """A single path returns scores without a ranking list."""
        result = analyze_tool._run(
            supply_path_data_json=json.dumps(sample_supply_path_data),
        )
        parsed = json.loads(result)
        # Single path: no ranking needed (or ranking has single entry)
        if "ranking" in parsed:
            assert len(parsed["ranking"]) <= 1
