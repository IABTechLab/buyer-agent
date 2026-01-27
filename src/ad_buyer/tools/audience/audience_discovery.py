# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Discovery Tool - Discover available audience signals from sellers."""

import asyncio
from typing import Any, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.ucp_client import UCPClient
from ...models.ucp import AudienceCapability, SignalType


class AudienceDiscoveryInput(BaseModel):
    """Input schema for audience discovery tool."""

    seller_endpoint: str = Field(
        description="Seller's capability discovery endpoint URL"
    )
    signal_types: Optional[list[str]] = Field(
        default=None,
        description="Filter by signal types: identity, contextual, reinforcement",
    )
    min_coverage: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Minimum coverage percentage required",
    )


class AudienceDiscoveryTool(BaseTool):
    """Discover available audience signals from sellers via UCP.

    This tool queries seller endpoints to discover their audience
    capabilities, including signal types, coverage, and UCP compatibility.
    """

    name: str = "discover_audience_capabilities"
    description: str = """Discover available audience capabilities from a seller.
    Returns a list of audience signals the seller can provide, including
    coverage percentages and UCP compatibility status. Use this to understand
    what targeting options are available before planning audiences."""
    args_schema: Type[BaseModel] = AudienceDiscoveryInput

    def _run(
        self,
        seller_endpoint: str,
        signal_types: Optional[list[str]] = None,
        min_coverage: Optional[float] = None,
    ) -> str:
        """Execute the audience discovery."""
        return asyncio.run(
            self._arun(seller_endpoint, signal_types, min_coverage)
        )

    async def _arun(
        self,
        seller_endpoint: str,
        signal_types: Optional[list[str]] = None,
        min_coverage: Optional[float] = None,
    ) -> str:
        """Async implementation of audience discovery."""
        client = UCPClient()

        try:
            capabilities = await client.discover_capabilities(seller_endpoint)
        except Exception as e:
            return f"Error discovering capabilities: {e}"
        finally:
            await client.close()

        if not capabilities:
            # Return mock capabilities for demonstration
            capabilities = self._get_mock_capabilities()

        # Filter by signal type if specified
        if signal_types:
            valid_types = set()
            for st in signal_types:
                try:
                    valid_types.add(SignalType(st.lower()))
                except ValueError:
                    pass

            if valid_types:
                capabilities = [
                    cap for cap in capabilities
                    if cap.signal_type in valid_types
                ]

        # Filter by minimum coverage
        if min_coverage is not None:
            capabilities = [
                cap for cap in capabilities
                if cap.coverage_percentage >= min_coverage
            ]

        return self._format_results(capabilities)

    def _get_mock_capabilities(self) -> list[AudienceCapability]:
        """Return mock capabilities for demonstration."""
        return [
            AudienceCapability(
                capability_id="cap_demo_age",
                name="Age Demographics",
                description="Age-based targeting using modeled data",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=75.0,
                available_segments=["18-24", "25-34", "35-44", "45-54", "55+"],
                taxonomy="IAB-1.0",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id="cap_demo_gender",
                name="Gender Demographics",
                description="Gender-based targeting using modeled data",
                signal_type=SignalType.IDENTITY,
                coverage_percentage=70.0,
                available_segments=["male", "female"],
                taxonomy="IAB-1.0",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id="cap_ctx_categories",
                name="Content Categories",
                description="IAB content category targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=95.0,
                available_segments=["IAB1", "IAB2", "IAB3", "IAB4", "IAB5"],
                taxonomy="IAB-2.2",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id="cap_ctx_keywords",
                name="Keyword Targeting",
                description="Content keyword targeting",
                signal_type=SignalType.CONTEXTUAL,
                coverage_percentage=90.0,
                available_segments=[],
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id="cap_int_purchase",
                name="Purchase Intent",
                description="In-market purchase intent signals",
                signal_type=SignalType.REINFORCEMENT,
                coverage_percentage=45.0,
                available_segments=["auto", "travel", "finance", "retail"],
                taxonomy="custom",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
            AudienceCapability(
                capability_id="cap_int_converters",
                name="Past Converters",
                description="Users who previously converted",
                signal_type=SignalType.REINFORCEMENT,
                coverage_percentage=15.0,
                available_segments=["converters_30d", "converters_90d"],
                taxonomy="custom",
                ucp_compatible=True,
                embedding_dimension=512,
            ),
        ]

    def _format_results(self, capabilities: list[AudienceCapability]) -> str:
        """Format capabilities as human-readable output."""
        if not capabilities:
            return "No audience capabilities found matching your criteria."

        # Group by signal type
        by_signal_type: dict[str, list[AudienceCapability]] = {}
        for cap in capabilities:
            signal = cap.signal_type.value
            if signal not in by_signal_type:
                by_signal_type[signal] = []
            by_signal_type[signal].append(cap)

        output = f"Found {len(capabilities)} audience capabilities:\n\n"

        for signal_type, caps in sorted(by_signal_type.items()):
            output += f"## {signal_type.upper()} SIGNALS\n"

            for cap in caps:
                ucp_status = "UCP" if cap.ucp_compatible else "NO-UCP"
                output += f"\n**{cap.name}** [{ucp_status}]\n"
                output += f"   ID: {cap.capability_id}\n"
                output += f"   Coverage: {cap.coverage_percentage:.0f}%\n"
                if cap.description:
                    output += f"   Description: {cap.description}\n"
                if cap.available_segments:
                    segments_preview = cap.available_segments[:5]
                    more = len(cap.available_segments) - 5
                    output += f"   Segments: {', '.join(segments_preview)}"
                    if more > 0:
                        output += f" (+{more} more)"
                    output += "\n"

            output += "\n"

        # Summary
        ucp_count = sum(1 for cap in capabilities if cap.ucp_compatible)
        avg_coverage = sum(cap.coverage_percentage for cap in capabilities) / len(capabilities)

        output += "---\n"
        output += f"Summary: {ucp_count}/{len(capabilities)} UCP-compatible, "
        output += f"avg coverage: {avg_coverage:.0f}%"

        return output
