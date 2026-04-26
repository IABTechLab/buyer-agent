"""Embedding evaluation harness — compares mock vs local vs hybrid quality.

Bead E2-3. The harness generates embeddings for a fixed corpus of audience
briefs under each mode, then computes:

- **Self-similarity stability**: same brief → same vector (deterministic)?
- **Distinctiveness**: different briefs → different vectors (cosine distance)?
- **Dimension consistency**: same dim per mode?

Mock embeddings are deterministic (SHA256-seeded) and pass self-similarity
trivially but score low on distinctiveness for similar-but-distinct briefs.
Local sentence-transformers should score higher distinctiveness on
semantically distinct briefs. The eval surfaces these differences so
downstream code (E2-4 threshold recalibration) can pick a threshold per mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from ad_buyer.clients.ucp_client import UCPClient
from ad_buyer.config.settings import settings


# Fixed corpus of audience briefs covering each of the 3 audience types and
# semantically related pairs (so distinctiveness has signal).
EMBEDDING_EVAL_FIXTURES: list[dict[str, Any]] = [
    {"name": "auto_intenders", "interest": "auto", "age": "25-54", "income": "high"},
    {"name": "auto_owners",    "interest": "auto", "age": "35-65", "income": "high"},
    {"name": "sports_fans",    "interest": "sports", "age": "18-44"},
    {"name": "news_readers",   "interest": "news",   "age": "35-65"},
    {"name": "young_gamers",   "interest": "gaming", "age": "18-24"},
]


@dataclass
class PerModeMetrics:
    """Metrics for a single embedding mode."""

    mode: str
    n_fixtures: int
    deterministic: bool          # repeat-call returns same vector for each fixture
    dimension: int               # all fixtures produce the same dim
    distinctiveness: float       # mean pairwise cosine distance across fixtures
    provenance: str              # provenance reported by the client

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "n_fixtures": self.n_fixtures,
            "deterministic": self.deterministic,
            "dimension": self.dimension,
            "distinctiveness": round(self.distinctiveness, 4),
            "provenance": self.provenance,
        }


@dataclass
class EvalReport:
    """Full evaluation report across all configured modes."""

    fixtures: list[dict[str, Any]] = field(default_factory=list)
    per_mode: list[PerModeMetrics] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixtures": self.fixtures,
            "per_mode": [m.as_dict() for m in self.per_mode],
        }


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """1 - cosine_similarity. 0 = identical, 1 = orthogonal, 2 = opposite."""

    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
    if not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 1.0
    return 1.0 - (dot / (na * nb))


def _eval_single_mode(
    mode: str,
    fixtures: list[dict[str, Any]],
) -> PerModeMetrics:
    """Run the eval for a single embedding mode."""

    client = UCPClient()
    with patch.object(settings, "embedding_mode", mode):
        # First pass: gather vectors
        first = [
            client.create_query_embedding_with_provenance(f) for f in fixtures
        ]
        # Second pass: gather again to check determinism
        second = [
            client.create_query_embedding_with_provenance(f) for f in fixtures
        ]

    deterministic = all(
        f.embedding.vector == s.embedding.vector for f, s in zip(first, second)
    )

    dims = {len(r.embedding.vector) for r in first}
    dimension = dims.pop() if len(dims) == 1 else -1

    # Distinctiveness: mean cosine distance between distinct fixture pairs.
    distances: list[float] = []
    for i in range(len(first)):
        for j in range(i + 1, len(first)):
            distances.append(
                _cosine_distance(first[i].embedding.vector, first[j].embedding.vector)
            )
    distinctiveness = sum(distances) / len(distances) if distances else 0.0

    # Provenance: should be consistent across fixtures within a mode.
    provs = {r.provenance for r in first}
    provenance = "/".join(sorted(provs))

    return PerModeMetrics(
        mode=mode,
        n_fixtures=len(fixtures),
        deterministic=deterministic,
        dimension=dimension,
        distinctiveness=distinctiveness,
        provenance=provenance,
    )


def evaluate_embedding_modes(
    modes: list[str] | None = None,
    fixtures: list[dict[str, Any]] | None = None,
) -> EvalReport:
    """Run the embedding-mode eval and return a structured report.

    Args:
        modes: list of `EMBEDDING_MODE` values to evaluate. Defaults to
            ["mock", "local", "advertiser", "hybrid"]. Local mode silently
            falls back to mock if sentence-transformers is unavailable.
        fixtures: corpus of audience briefs. Defaults to
            `EMBEDDING_EVAL_FIXTURES`.

    Returns:
        `EvalReport` with per-mode metrics suitable for serialization /
        threshold-recalibration analysis (E2-4).
    """

    modes = modes or ["mock", "local", "advertiser", "hybrid"]
    fixtures = fixtures or EMBEDDING_EVAL_FIXTURES

    return EvalReport(
        fixtures=list(fixtures),
        per_mode=[_eval_single_mode(m, fixtures) for m in modes],
    )
