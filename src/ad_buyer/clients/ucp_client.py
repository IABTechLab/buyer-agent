# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""UCP (User Context Protocol) Client for audience signal exchange.

This client handles the exchange of embeddings between buyer and seller agents
following the IAB Tech Lab UCP specification.

NOTE: This module implements IAB Agentic Audiences (formerly User Context
Protocol / UCP). Public-surface naming uses "Agentic Audiences (UCP)" per
proposal AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md §5.6 -- the code
keeps `ucp_*` names internally to avoid a churning rename of a still-DRAFT
spec, but readers searching for either term land here.
"""

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..models.ucp import (
    AudienceCapability,
    AudienceValidationResult,
    EmbeddingType,
    SignalType,
    SimilarityMetric,
    UCPConsent,
    UCPEmbedding,
    UCPModelDescriptor,
)

logger = logging.getLogger(__name__)

# UCP Content-Type header
UCP_CONTENT_TYPE = "application/vnd.ucp.embedding+json; v=1"

# Embedding provenance literal -- mirrors ComplianceContext.embedding_provenance.
EmbeddingProvenance = Literal[
    "mock", "local_buyer", "advertiser_supplied", "hosted_external"
]

# Local model details for "local" / "hybrid" embedding modes.
# Locked in docs/decisions/EMBEDDING_STRATEGY_2026-04-25.md (E2-1).
LOCAL_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
LOCAL_EMBEDDING_MODEL_DIM = 384

# Process-wide cached SentenceTransformer instance. Lazy-loaded on first
# use to avoid paying ~80MB model download cost at import time.
_LOCAL_MODEL: Any = None
_LOCAL_MODEL_LOAD_FAILED = False


def _get_local_embedding_model() -> Any:
    """Lazy-load and cache the local SentenceTransformer model.

    Returns the model on success, or None if sentence-transformers is not
    installed or the model fails to load (e.g. download blocked in CI).
    """
    global _LOCAL_MODEL, _LOCAL_MODEL_LOAD_FAILED
    if _LOCAL_MODEL is not None:
        return _LOCAL_MODEL
    if _LOCAL_MODEL_LOAD_FAILED:
        return None
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _LOCAL_MODEL = SentenceTransformer(LOCAL_EMBEDDING_MODEL_NAME)
        return _LOCAL_MODEL
    except Exception as exc:  # ImportError, network errors, etc.
        logger.warning(
            "Local embedding model unavailable (%s); falling back to mock. "
            "Install with: pip install 'ad-buyer-system[embeddings]'",
            exc,
        )
        _LOCAL_MODEL_LOAD_FAILED = True
        return None


@dataclass
class QueryEmbeddingResult:
    """Result of `create_query_embedding_with_provenance`.

    Carries the embedding vector together with provenance metadata so
    downstream code can record where the bytes came from in the
    ComplianceContext (E2-7 Gap 6).
    """

    embedding: UCPEmbedding
    provenance: EmbeddingProvenance
    dimension: int


class UCPExchangeResult:
    """Result of a UCP embedding exchange."""

    def __init__(
        self,
        success: bool,
        similarity_score: float | None = None,
        buyer_embedding: UCPEmbedding | None = None,
        seller_embedding: UCPEmbedding | None = None,
        matched_capabilities: list[str] | None = None,
        error: str | None = None,
    ):
        self.success = success
        self.similarity_score = similarity_score
        self.buyer_embedding = buyer_embedding
        self.seller_embedding = seller_embedding
        self.matched_capabilities = matched_capabilities or []
        self.error = error


class UCPClient:
    """Client for UCP embedding exchange with seller endpoints.

    Handles:
    - Sending embeddings to seller endpoints
    - Receiving embeddings from sellers
    - Computing similarity between embeddings
    - Discovering seller audience capabilities
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
        default_dimension: int = 512,
    ):
        """Initialize the UCP client.

        Args:
            base_url: Base URL for UCP endpoints (if not per-request)
            timeout: Request timeout in seconds
            default_dimension: Default embedding dimension to use
        """
        self._base_url = base_url
        self._timeout = timeout
        self._default_dimension = default_dimension
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def send_embedding(
        self,
        embedding: UCPEmbedding,
        endpoint: str,
    ) -> dict[str, Any]:
        """Send an embedding to a seller's UCP endpoint.

        Args:
            embedding: The embedding to send
            endpoint: Full URL of the seller's UCP endpoint

        Returns:
            Response from the seller endpoint
        """
        client = await self._get_client()

        headers = {
            "Content-Type": UCP_CONTENT_TYPE,
            "Accept": UCP_CONTENT_TYPE,
        }

        try:
            response = await client.post(
                endpoint,
                json=embedding.model_dump(by_alias=True, mode="json"),
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"UCP send failed: {e.response.status_code} - {e.response.text}")
            return {"error": str(e), "status_code": e.response.status_code}
        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"UCP send error: {e}")
            return {"error": str(e)}

    async def receive_embedding(
        self,
        endpoint: str,
        query_params: dict[str, Any] | None = None,
    ) -> UCPEmbedding | None:
        """Receive an embedding from a seller's UCP endpoint.

        Args:
            endpoint: Full URL of the seller's UCP endpoint
            query_params: Optional query parameters

        Returns:
            UCPEmbedding if successful, None otherwise
        """
        client = await self._get_client()

        headers = {"Accept": UCP_CONTENT_TYPE}

        try:
            response = await client.get(
                endpoint,
                params=query_params,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            return UCPEmbedding.model_validate(data)

        except httpx.HTTPStatusError as e:
            logger.error(f"UCP receive failed: {e.response.status_code}")
            return None
        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"UCP receive error: {e}")
            return None

    async def discover_capabilities(
        self,
        endpoint: str,
    ) -> list[AudienceCapability]:
        """Discover audience capabilities from a seller endpoint.

        Args:
            endpoint: Seller's capability discovery endpoint

        Returns:
            List of available audience capabilities
        """
        client = await self._get_client()

        try:
            response = await client.get(endpoint)
            response.raise_for_status()
            data = response.json()

            capabilities = []
            for cap_data in data.get("capabilities", []):
                try:
                    capabilities.append(AudienceCapability.model_validate(cap_data))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse capability: {e}")

            return capabilities

        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"Capability discovery failed: {e}")
            return []

    def compute_similarity(
        self,
        emb1: UCPEmbedding,
        emb2: UCPEmbedding,
        metric: SimilarityMetric | None = None,
    ) -> float:
        """Compute similarity between two embeddings.

        Args:
            emb1: First embedding
            emb2: Second embedding
            metric: Similarity metric to use (defaults to model's recommendation)

        Returns:
            Similarity score (0-1 for cosine, unbounded for dot/L2)
        """
        if emb1.dimension != emb2.dimension:
            logger.warning(f"Dimension mismatch: {emb1.dimension} vs {emb2.dimension}")
            return 0.0

        # Use recommended metric from model descriptor, or cosine as default
        if metric is None:
            metric = emb1.model_descriptor.metric

        v1 = emb1.vector
        v2 = emb2.vector

        if metric == SimilarityMetric.COSINE:
            return self._cosine_similarity(v1, v2)
        elif metric == SimilarityMetric.DOT:
            return self._dot_product(v1, v2)
        elif metric == SimilarityMetric.L2:
            return self._l2_distance(v1, v2)
        else:
            return self._cosine_similarity(v1, v2)

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Compute cosine similarity."""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot / (norm1 * norm2)

    def _dot_product(self, v1: list[float], v2: list[float]) -> float:
        """Compute dot product."""
        return sum(a * b for a, b in zip(v1, v2))

    def _l2_distance(self, v1: list[float], v2: list[float]) -> float:
        """Compute L2 (Euclidean) distance.

        Note: Returns distance, not similarity. Lower is more similar.
        """
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))

    async def exchange_embeddings(
        self,
        buyer_embedding: UCPEmbedding,
        seller_endpoint: str,
    ) -> UCPExchangeResult:
        """Perform a full embedding exchange with a seller.

        Sends the buyer embedding and receives seller's embedding,
        then computes similarity.

        Args:
            buyer_embedding: Buyer's audience intent embedding
            seller_endpoint: Seller's UCP exchange endpoint

        Returns:
            UCPExchangeResult with similarity score and embeddings
        """
        # Send buyer embedding and expect seller embedding in response
        client = await self._get_client()

        headers = {
            "Content-Type": UCP_CONTENT_TYPE,
            "Accept": UCP_CONTENT_TYPE,
        }

        try:
            response = await client.post(
                seller_endpoint,
                json=buyer_embedding.model_dump(by_alias=True, mode="json"),
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

            # Parse seller's embedding from response
            seller_embedding = None
            if "embedding" in data:
                seller_embedding = UCPEmbedding.model_validate(data["embedding"])

            # Compute similarity if we got seller's embedding
            similarity_score = None
            if seller_embedding:
                similarity_score = self.compute_similarity(buyer_embedding, seller_embedding)

            return UCPExchangeResult(
                success=True,
                similarity_score=similarity_score,
                buyer_embedding=buyer_embedding,
                seller_embedding=seller_embedding,
                matched_capabilities=data.get("matched_capabilities", []),
            )

        except (httpx.HTTPError, ValueError) as e:
            logger.error(f"Embedding exchange failed: {e}")
            return UCPExchangeResult(
                success=False,
                error=str(e),
            )

    def create_embedding(
        self,
        vector: list[float],
        embedding_type: EmbeddingType,
        signal_type: SignalType,
        consent: UCPConsent | None = None,
        model_id: str = "ucp-embedding-v1",
        model_version: str = "1.0.0",
    ) -> UCPEmbedding:
        """Create a UCPEmbedding from a vector.

        Helper method to construct properly formatted embeddings.

        Args:
            vector: The embedding vector
            embedding_type: Type of embedding
            signal_type: UCP signal type
            consent: Consent information (required)
            model_id: Model identifier
            model_version: Model version

        Returns:
            Properly formatted UCPEmbedding
        """
        dimension = len(vector)

        if consent is None:
            # Create default consent with minimal permissions
            consent = UCPConsent(
                framework="IAB-TCFv2",
                permissible_uses=["measurement"],
                ttl_seconds=3600,
            )

        model_descriptor = UCPModelDescriptor(
            id=model_id,
            version=model_version,
            dimension=dimension,
            metric=SimilarityMetric.COSINE,
        )

        return UCPEmbedding(
            embedding_type=embedding_type,
            signal_type=signal_type,
            vector=vector,
            dimension=dimension,
            model_descriptor=model_descriptor,
            consent=consent,
        )

    def create_query_embedding(
        self,
        audience_requirements: dict[str, Any],
        consent: UCPConsent | None = None,
        advertiser_vector: list[float] | None = None,
    ) -> UCPEmbedding:
        """Create a query embedding from audience requirements.

        Backward-compatible entry point. Honors `settings.embedding_mode`
        per E2-1's locked decision (mock | local | advertiser | hybrid).
        For provenance metadata, use `create_query_embedding_with_provenance`.

        Args:
            audience_requirements: Audience targeting requirements
            consent: Consent information
            advertiser_vector: Optional advertiser-supplied vector. Used
                when `embedding_mode` is "advertiser" or "hybrid".

        Returns:
            UCPEmbedding representing the audience intent
        """
        return self.create_query_embedding_with_provenance(
            audience_requirements,
            consent=consent,
            advertiser_vector=advertiser_vector,
        ).embedding

    def create_query_embedding_with_provenance(
        self,
        audience_requirements: dict[str, Any],
        consent: UCPConsent | None = None,
        advertiser_vector: list[float] | None = None,
    ) -> "QueryEmbeddingResult":
        """Create a query embedding plus provenance metadata.

        Selects the embedding source per `settings.embedding_mode`:
        - "advertiser" / "hybrid" with advertiser_vector → use it verbatim
        - "local" / "hybrid" → sentence-transformers local model
        - "mock" or any fallback → deterministic SHA256-seeded synthetic

        Provenance is also reported so downstream code (ComplianceContext
        per E2-7 Gap 6) can record where the bytes came from.
        """
        from ad_buyer.config.settings import settings as _settings

        mode = _settings.embedding_mode
        vector: list[float]
        provenance: EmbeddingProvenance

        # Advertiser path: usable when mode permits + vector supplied.
        if mode in ("advertiser", "hybrid") and advertiser_vector is not None:
            if not (256 <= len(advertiser_vector) <= 1024):
                logger.warning(
                    "Advertiser-supplied vector dim=%d outside spec range "
                    "[256, 1024]; falling back",
                    len(advertiser_vector),
                )
            else:
                vector = list(advertiser_vector)
                provenance = "advertiser_supplied"
                return QueryEmbeddingResult(
                    embedding=self.create_embedding(
                        vector=vector,
                        embedding_type=EmbeddingType.QUERY,
                        signal_type=SignalType.CONTEXTUAL,
                        consent=consent,
                    ),
                    provenance=provenance,
                    dimension=len(vector),
                )

        # Local path: sentence-transformers if available + mode permits.
        if mode in ("local", "hybrid"):
            model = _get_local_embedding_model()
            if model is not None:
                req_str = str(sorted(audience_requirements.items()))
                raw = model.encode(req_str, convert_to_numpy=True)
                vector = [float(x) for x in raw.tolist()]
                provenance = "local_buyer"
                return QueryEmbeddingResult(
                    embedding=self.create_embedding(
                        vector=vector,
                        embedding_type=EmbeddingType.QUERY,
                        signal_type=SignalType.CONTEXTUAL,
                        consent=consent,
                    ),
                    provenance=provenance,
                    dimension=len(vector),
                )

        # Mock fallback (also handles mode="mock" explicitly).
        vector = self._generate_synthetic_embedding(
            audience_requirements,
            self._default_dimension,
        )
        provenance = "mock"
        return QueryEmbeddingResult(
            embedding=self.create_embedding(
                vector=vector,
                embedding_type=EmbeddingType.QUERY,
                signal_type=SignalType.CONTEXTUAL,
                consent=consent,
            ),
            provenance=provenance,
            dimension=len(vector),
        )

    def _generate_synthetic_embedding(
        self,
        requirements: dict[str, Any],
        dimension: int,
    ) -> list[float]:
        """Generate a synthetic embedding from requirements.

        This is a placeholder - in production, use a trained embedding model.
        """
        import hashlib

        # Create a deterministic seed from requirements
        req_str = str(sorted(requirements.items()))
        seed = int(hashlib.sha256(req_str.encode()).hexdigest()[:8], 16)

        # Generate pseudo-random but deterministic vector using local instance
        # (avoids setting global random state, which is not thread-safe)
        import random

        rng = random.Random(seed)

        # Generate normalized vector
        vector = [rng.gauss(0, 1) for _ in range(dimension)]
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    async def validate_audience_with_seller(
        self,
        audience_requirements: dict[str, Any],
        seller_endpoint: str,
        consent: UCPConsent | None = None,
    ) -> AudienceValidationResult:
        """Validate audience requirements against seller capabilities.

        Args:
            audience_requirements: Buyer's targeting requirements
            seller_endpoint: Seller's validation endpoint
            consent: Consent information

        Returns:
            AudienceValidationResult with coverage and gaps
        """
        # Create query embedding
        query_embedding = self.create_query_embedding(audience_requirements, consent)

        # Exchange embeddings
        exchange_result = await self.exchange_embeddings(query_embedding, seller_endpoint)

        if not exchange_result.success:
            return AudienceValidationResult(
                validation_status="invalid",
                targeting_compatible=False,
                validation_notes=[f"Exchange failed: {exchange_result.error}"],
            )

        # Determine validation status based on similarity
        similarity = exchange_result.similarity_score or 0.0

        if similarity >= 0.7:
            status = "valid"
            compatible = True
        elif similarity >= 0.5:
            status = "partial_match"
            compatible = True
        elif similarity >= 0.3:
            status = "partial_match"
            compatible = False
        else:
            status = "no_match"
            compatible = False

        return AudienceValidationResult(
            validation_status=status,
            overall_coverage_percentage=similarity * 100,
            matched_capabilities=exchange_result.matched_capabilities,
            ucp_similarity_score=similarity,
            targeting_compatible=compatible,
            validation_notes=[
                f"UCP similarity: {similarity:.2f}",
                f"Matched {len(exchange_result.matched_capabilities)} capabilities",
            ],
        )
