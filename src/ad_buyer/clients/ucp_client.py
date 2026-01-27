# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""UCP (User Context Protocol) Client for audience signal exchange.

This client handles the exchange of embeddings between buyer and seller agents
following the IAB Tech Lab UCP specification.
"""

import logging
import math
from datetime import datetime
from typing import Any, Optional

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


class UCPExchangeResult:
    """Result of a UCP embedding exchange."""

    def __init__(
        self,
        success: bool,
        similarity_score: Optional[float] = None,
        buyer_embedding: Optional[UCPEmbedding] = None,
        seller_embedding: Optional[UCPEmbedding] = None,
        matched_capabilities: list[str] | None = None,
        error: Optional[str] = None,
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
        base_url: Optional[str] = None,
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
        self._client: Optional[httpx.AsyncClient] = None

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
        except Exception as e:
            logger.error(f"UCP send error: {e}")
            return {"error": str(e)}

    async def receive_embedding(
        self,
        endpoint: str,
        query_params: Optional[dict[str, Any]] = None,
    ) -> Optional[UCPEmbedding]:
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
        except Exception as e:
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
                except Exception as e:
                    logger.warning(f"Failed to parse capability: {e}")

            return capabilities

        except Exception as e:
            logger.error(f"Capability discovery failed: {e}")
            return []

    def compute_similarity(
        self,
        emb1: UCPEmbedding,
        emb2: UCPEmbedding,
        metric: Optional[SimilarityMetric] = None,
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
            logger.warning(
                f"Dimension mismatch: {emb1.dimension} vs {emb2.dimension}"
            )
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
                similarity_score = self.compute_similarity(
                    buyer_embedding, seller_embedding
                )

            return UCPExchangeResult(
                success=True,
                similarity_score=similarity_score,
                buyer_embedding=buyer_embedding,
                seller_embedding=seller_embedding,
                matched_capabilities=data.get("matched_capabilities", []),
            )

        except Exception as e:
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
        consent: Optional[UCPConsent] = None,
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
        consent: Optional[UCPConsent] = None,
    ) -> UCPEmbedding:
        """Create a query embedding from audience requirements.

        Generates a synthetic embedding representing the audience intent.
        In production, this would use a trained embedding model.

        Args:
            audience_requirements: Audience targeting requirements
            consent: Consent information

        Returns:
            UCPEmbedding representing the audience intent
        """
        # Generate a deterministic embedding based on requirements
        # In production, this would use a trained model
        vector = self._generate_synthetic_embedding(
            audience_requirements,
            self._default_dimension,
        )

        return self.create_embedding(
            vector=vector,
            embedding_type=EmbeddingType.QUERY,
            signal_type=SignalType.CONTEXTUAL,
            consent=consent,
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
        import struct

        # Create a deterministic seed from requirements
        req_str = str(sorted(requirements.items()))
        seed = int(hashlib.sha256(req_str.encode()).hexdigest()[:8], 16)

        # Generate pseudo-random but deterministic vector
        import random
        random.seed(seed)

        # Generate normalized vector
        vector = [random.gauss(0, 1) for _ in range(dimension)]
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0:
            vector = [v / norm for v in vector]

        return vector

    async def validate_audience_with_seller(
        self,
        audience_requirements: dict[str, Any],
        seller_endpoint: str,
        consent: Optional[UCPConsent] = None,
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
        query_embedding = self.create_query_embedding(
            audience_requirements, consent
        )

        # Exchange embeddings
        exchange_result = await self.exchange_embeddings(
            query_embedding, seller_endpoint
        )

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
