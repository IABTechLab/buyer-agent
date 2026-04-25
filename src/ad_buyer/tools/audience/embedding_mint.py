# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Embedding Mint Tool - Mint a draft Agentic Audience reference (mock).

The Audience Planner agent uses this tool when the brief references
advertiser-first-party data ("our converters", "lookalike of last
campaign") that does not resolve against the static IAB Audience or
Content taxonomies. The tool turns a free-text description into an
`AudienceRef` of `type="agentic"` carrying an `emb://` identifier and
the consent context required by the schema.

NOTE: This is a MOCK implementation. The underlying vector is generated
by `UCPClient.create_query_embedding()`, which is the SHA256-seeded
deterministic mock at `clients/ucp_client.py:394-421` -- not a trained
embedding model. The replacement with a real model is tracked as
follow-up Epic 2 (proposal §6.5 + bead §22). Every emitted ref carries
an `emb://` URI prefix to make the mock provenance unambiguous in logs
and debugger output.

References:
- Proposal §5.5 step 2 (planner picks Agentic when brief references
  advertiser-first-party data).
- Proposal §5.6 (Agentic Audiences carrier; consent context required).
- Proposal §6.5 / bead §22 (real model is a follow-up epic).
"""

from __future__ import annotations

import hashlib
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from ...clients.ucp_client import UCPClient
from ...models.audience_plan import AudienceRef, ComplianceContext


# Public label exposed on the tool so debug surfaces (and §13a audit
# trail consumers) can render the mock provenance without poking at
# implementation details. Bumped when bead §22 swaps in a real model.
EMBEDDING_MODE_LABEL_MOCK = "MOCK (SHA256-seeded; bead §22 follow-up)"


class EmbeddingMintInput(BaseModel):
    """Input schema for the embedding mint tool."""

    name: str = Field(
        description=(
            "Short identifier for the audience being minted "
            "(e.g. 'last-campaign-converters', 'high-ltv-lookalike')."
        )
    )
    description: str = Field(
        default="",
        description=(
            "Free-text description of the target audience. Combined with "
            "`name` to deterministically seed the mock embedding."
        ),
    )
    jurisdiction: str = Field(
        default="GLOBAL",
        description=(
            "Jurisdiction code for the consent context "
            "(e.g. 'US', 'EU', 'GLOBAL')."
        ),
    )
    consent_framework: str = Field(
        default="advertiser-1p",
        description=(
            "Consent framework backing the mint: 'IAB-TCFv2', 'GPP', "
            "'advertiser-1p', or 'none'."
        ),
    )


class EmbeddingMintTool(BaseTool):
    """Mint a mock Agentic Audience reference.

    Produces an `AudienceRef` with:
    - `type="agentic"`,
    - `identifier="emb://<sha256>"` keyed off the supplied name+description,
    - `taxonomy="agentic-audiences"`,
    - `version="draft-2026-01"`,
    - `source="inferred"`,
    - sensible default `compliance_context`.

    Internally generates the underlying vector via
    `UCPClient.create_query_embedding()` so the same name+description always
    maps to the same embedding identity (this matters because the planner
    might call the tool repeatedly across reasoning passes and we don't
    want spurious ref-identity churn in the logs).

    The full `UCPEmbedding` itself is not surfaced here -- the carrier
    (UCPClient) holds the vector when needed; the planner only needs the
    `AudienceRef` handle for plan composition.
    """

    name: str = "mint_agentic_embedding"
    description: str = (
        "Mint a draft Agentic Audience reference for advertiser-first-party "
        "data that does not resolve against static IAB taxonomies. Inputs: "
        "`name`, optional `description`, optional `jurisdiction` (default "
        "'GLOBAL'), optional `consent_framework` (default 'advertiser-1p'). "
        "Returns an `AudienceRef` with type='agentic', an emb:// identifier, "
        "and a populated compliance_context. NOTE: the underlying embedding "
        "is currently a SHA256-seeded mock (bead §22 will swap in a real "
        "model)."
    )
    args_schema: Type[BaseModel] = EmbeddingMintInput

    # Public attribute so the planner / debugger can render the mock
    # provenance without reaching into private state.
    embedding_mode_label: str = EMBEDDING_MODE_LABEL_MOCK

    # Pydantic config: allow arbitrary attribute-style access on the
    # client field (httpx.AsyncClient is not Pydantic-friendly).
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Optional injected client; when None, a fresh UCPClient is constructed
    # per call. Tests pass an explicit client to assert the mock-provenance
    # plumbing.
    ucp_client: UCPClient | None = None

    def _run(
        self,
        name: str,
        description: str = "",
        jurisdiction: str = "GLOBAL",
        consent_framework: str = "advertiser-1p",
    ) -> str:
        """Mint a ref and return a human-readable rendering for the agent."""

        ref = self.mint(
            name=name,
            description=description,
            jurisdiction=jurisdiction,
            consent_framework=consent_framework,
        )
        return self._format_ref(ref)

    # ------------------------------------------------------------------
    # Public typed-mint helper
    # ------------------------------------------------------------------

    def mint(
        self,
        *,
        name: str,
        description: str = "",
        jurisdiction: str = "GLOBAL",
        consent_framework: str = "advertiser-1p",
    ) -> AudienceRef:
        """Mint and return the typed `AudienceRef`.

        Exposed separately from `_run()` so callers (the planner factory,
        tests, future programmatic consumers) can get the typed object
        without re-parsing the agent-readable string.
        """

        # Build the deterministic identifier first. We hash name +
        # description because they together define the audience identity;
        # the consent fields are policy and may vary across activations
        # without the underlying audience changing.
        seed_payload = f"{name}\x00{description}".encode("utf-8")
        digest = hashlib.sha256(seed_payload).hexdigest()
        identifier = f"emb://{digest}"

        # Generate the underlying mock vector via the existing UCPClient
        # so the load-bearing-fake provenance is honest: this tool does
        # not introduce a *second* mock pathway; it delegates to the one
        # the rest of the buyer code already trusts.
        client = self.ucp_client or UCPClient()
        client.create_query_embedding(
            audience_requirements={
                "name": name,
                "description": description,
                "consent_framework": consent_framework,
                "jurisdiction": jurisdiction,
            }
        )

        compliance = ComplianceContext(
            jurisdiction=jurisdiction,
            consent_framework=consent_framework,
            consent_string_ref=None,
            attestation=None,
        )

        return AudienceRef(
            type="agentic",
            identifier=identifier,
            taxonomy="agentic-audiences",
            version="draft-2026-01",
            source="inferred",
            confidence=None,
            compliance_context=compliance,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _format_ref(ref: AudienceRef) -> str:
        """Render the minted ref as agent-readable text."""

        cc: Any = ref.compliance_context
        cc_lines = (
            [
                f"  jurisdiction: {cc.jurisdiction}",
                f"  consent_framework: {cc.consent_framework}",
            ]
            if cc is not None
            else ["  (no compliance_context)"]
        )

        lines = [
            "MINTED",
            f"  type: {ref.type}",
            f"  identifier: {ref.identifier}",
            f"  taxonomy: {ref.taxonomy}",
            f"  version: {ref.version}",
            f"  source: {ref.source}",
            f"  embedding_mode: {EMBEDDING_MODE_LABEL_MOCK}",
            "  compliance_context:",
            *cc_lines,
        ]
        return "\n".join(lines)
