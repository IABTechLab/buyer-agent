# End-to-end test against the production Mixpeek API.
#
# Exercises the full buyer-agent contextual enrichment flow:
#   1. Health check (public endpoint)
#   2. MCP tools discovery (public endpoint)
#   3. IAB taxonomy classification of real ad content
#   4. Brand-safety scoring of safe and sensitive content
#   5. Contextual inventory search via retriever pipeline
#
# Requires:
#   MIXPEEK_API_KEY — a valid Mixpeek API key
#   MIXPEEK_NAMESPACE — namespace with IAB data (default: golden_adtech_iab)
#
# Run:
#   MIXPEEK_API_KEY=mxp_sk_... pytest tests/e2e/test_mixpeek_production.py -v

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from ad_buyer.clients.mixpeek_client import MixpeekClient, MixpeekError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_KEY = os.environ.get("MIXPEEK_API_KEY", "")
BASE_URL = os.environ.get("MIXPEEK_BASE_URL", "https://api.mixpeek.com")
NAMESPACE = os.environ.get("MIXPEEK_NAMESPACE", "golden_adtech_iab")

# Known retriever in golden_adtech_iab namespace
IAB_TEXT_RETRIEVER = os.environ.get(
    "MIXPEEK_IAB_RETRIEVER_ID", "ret_f7fbefced358bd"
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not API_KEY, reason="MIXPEEK_API_KEY not set"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    c = MixpeekClient(
        api_key=API_KEY,
        base_url=BASE_URL,
        namespace=NAMESPACE,
    )
    yield c
    await c.close()


# ---------------------------------------------------------------------------
# 1. Health & Discovery
# ---------------------------------------------------------------------------

class TestHealthAndDiscovery:
    @pytest.mark.asyncio
    async def test_mcp_health(self, client: MixpeekClient):
        """MCP server health check returns healthy status."""
        result = await client.health()
        assert result["status"] == "healthy"
        assert result["tools_count"] >= 40  # currently 48

    @pytest.mark.asyncio
    async def test_mcp_tools_list(self, client: MixpeekClient):
        """Public tools endpoint returns the full tool catalog."""
        tools = await client.get_tools()
        assert len(tools) >= 40
        names = {t["name"] for t in tools}
        # Spot-check a few expected tools
        assert "create_namespace" in names
        assert "execute_retriever" in names

    @pytest.mark.asyncio
    async def test_list_retrievers(self, client: MixpeekClient):
        """Namespace has IAB retriever pipelines."""
        retrievers = await client.list_retrievers()
        assert len(retrievers) > 0
        names = {r["retriever_name"] for r in retrievers}
        assert any("iab" in n.lower() for n in names), (
            f"No IAB retriever found. Available: {names}"
        )


# ---------------------------------------------------------------------------
# 2. IAB Content Classification
# ---------------------------------------------------------------------------

class TestIABClassification:
    @pytest.mark.asyncio
    async def test_classify_sports_content(self, client: MixpeekClient):
        """NFL content classifies as Sports > American Football."""
        result = await client.classify_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "Breaking: NFL playoff scores and highlights from Sunday "
                "night football. Tom Brady analysis and Super Bowl predictions."
            ),
        )
        docs = result["documents"]
        assert len(docs) > 0

        top = docs[0]
        assert top["score"] > 0.80
        assert "Sports" in top["iab_path"]
        # Top result should be American Football or Sports
        assert top["iab_category_name"] in (
            "American Football", "Sports", "College Football",
        )

    @pytest.mark.asyncio
    async def test_classify_automotive_content(self, client: MixpeekClient):
        """Luxury car review classifies as Automotive > Luxury Cars."""
        result = await client.classify_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "The 2026 Mercedes-Benz S-Class review: luxury sedan with "
                "cutting-edge autonomous driving features, premium leather "
                "interior, and a 496-horsepower twin-turbo V8 engine."
            ),
        )
        docs = result["documents"]
        assert len(docs) > 0

        top = docs[0]
        assert top["score"] > 0.80
        assert "Automotive" in top["iab_path"]
        # Top result should be an Automotive subcategory
        assert "Automotive" in top["iab_path"]

    @pytest.mark.asyncio
    async def test_classify_food_content(self, client: MixpeekClient):
        """Cooking recipe classifies as Food & Drink > Cooking."""
        result = await client.classify_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "Easy homemade pasta recipe: combine fresh eggs, semolina "
                "flour, and olive oil. Roll the dough thin, cut into "
                "fettuccine, and cook in salted boiling water for 3 minutes."
            ),
        )
        docs = result["documents"]
        assert len(docs) > 0

        top = docs[0]
        assert top["score"] > 0.80
        # Should be in Food & Drink or Cooking
        paths_flat = [
            cat for d in docs[:3] for cat in d.get("iab_path", [])
        ]
        assert "Food & Drink" in paths_flat or "Cooking" in paths_flat

    @pytest.mark.asyncio
    async def test_classify_technology_content(self, client: MixpeekClient):
        """AI/ML content classifies as Technology > Artificial Intelligence."""
        result = await client.classify_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "Artificial intelligence and machine learning are "
                "transforming enterprise software. New AI startups raised "
                "$10B in Q1 2026 for large language model development."
            ),
        )
        docs = result["documents"]
        assert len(docs) > 0

        top = docs[0]
        assert top["score"] > 0.85
        assert top["iab_category_name"] == "Artificial Intelligence"
        assert "Technology & Computing" in top["iab_path"]

    @pytest.mark.asyncio
    async def test_classify_returns_hierarchical_paths(self, client: MixpeekClient):
        """Results include full IAB hierarchy: tier, path, category name."""
        result = await client.classify_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            text="Professional basketball NBA playoffs Lakers vs Celtics",
        )
        doc = result["documents"][0]
        assert "iab_category_name" in doc
        assert "iab_path" in doc
        assert "iab_tier" in doc
        assert isinstance(doc["iab_path"], list)
        assert doc["iab_tier"] in (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# 3. Brand Safety
# ---------------------------------------------------------------------------

class TestBrandSafety:
    @pytest.mark.asyncio
    async def test_safe_content(self, client: MixpeekClient):
        """Benign sports content is flagged as safe."""
        result = await client.check_brand_safety(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "Local high school basketball team wins state championship "
                "in an exciting overtime game at the civic center."
            ),
        )
        assert result["safe"] is True
        assert result["risk_level"] == "low"
        assert len(result["flagged_categories"]) == 0

    @pytest.mark.asyncio
    async def test_gambling_content_flagged(self, client: MixpeekClient):
        """Gambling content is flagged as brand-unsafe."""
        result = await client.check_brand_safety(
            retriever_id=IAB_TEXT_RETRIEVER,
            text=(
                "Online poker tournament with $1M prize pool. Texas Hold'em "
                "strategy guide for casino gambling. Bet on sports with our "
                "new odds calculator."
            ),
        )
        assert result["safe"] is False
        assert result["risk_level"] in ("medium", "high")
        flagged_names = [c["category"] for c in result["flagged_categories"]]
        assert any(
            cat in flagged_names
            for cat in (
                "Poker and Professional Gambling",
                "Casinos & Gambling",
                "Casino Games",
            )
        ), f"Expected gambling categories, got: {flagged_names}"

    @pytest.mark.asyncio
    async def test_brand_safety_threshold(self, client: MixpeekClient):
        """Higher threshold filters out lower-confidence matches."""
        result_low = await client.check_brand_safety(
            retriever_id=IAB_TEXT_RETRIEVER,
            text="Online poker tournament guide",
            threshold=0.70,
        )
        result_high = await client.check_brand_safety(
            retriever_id=IAB_TEXT_RETRIEVER,
            text="Online poker tournament guide",
            threshold=0.95,
        )
        # More categories pass at lower threshold
        assert len(result_low["categories"]) >= len(result_high["categories"])


# ---------------------------------------------------------------------------
# 4. Contextual Search
# ---------------------------------------------------------------------------

class TestContextualSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self, client: MixpeekClient):
        """Contextual search returns ranked results with scores."""
        result = await client.search_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            query="sports and athletics",
            limit=5,
        )
        assert "documents" in result
        docs = result["documents"]
        assert len(docs) > 0

        # Each result has a score
        for d in docs:
            assert "score" in d
            assert d["score"] > 0

    @pytest.mark.asyncio
    async def test_search_results_have_iab_metadata(self, client: MixpeekClient):
        """Search results include IAB category metadata."""
        result = await client.search_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            query="technology computing artificial intelligence",
            limit=3,
        )
        doc = result["documents"][0]
        assert "iab_category_name" in doc
        assert "iab_path" in doc
        assert "score" in doc

    @pytest.mark.asyncio
    async def test_search_returns_iab_enriched_results(self, client: MixpeekClient):
        """Search results contain IAB enrichment from the retriever."""
        result = await client.search_content(
            retriever_id=IAB_TEXT_RETRIEVER,
            query="food cooking recipes",
        )
        docs = result["documents"]
        assert len(docs) > 0
        # Results from IAB retriever have category metadata
        for d in docs[:3]:
            assert "iab_category_name" in d
            assert "iab_path" in d


# ---------------------------------------------------------------------------
# 5. Error Handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_invalid_retriever_id(self, client: MixpeekClient):
        """Invalid retriever ID raises MixpeekError."""
        with pytest.raises(MixpeekError) as exc_info:
            await client.classify_content(
                retriever_id="ret_nonexistent",
                text="test content",
            )
        assert exc_info.value.status_code in (404, 400, 422)

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        """Invalid API key raises MixpeekError."""
        bad_client = MixpeekClient(
            api_key="invalid_key",
            namespace=NAMESPACE,
        )
        try:
            with pytest.raises(MixpeekError) as exc_info:
                await bad_client.list_retrievers()
            assert exc_info.value.status_code in (401, 403, 404)
        finally:
            await bad_client.close()
