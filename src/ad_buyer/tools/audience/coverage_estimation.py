# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Coverage Estimation Tool - Estimate audience coverage for targeting."""

import asyncio
from typing import Any, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...models.ucp import CoverageEstimate


class CoverageEstimationInput(BaseModel):
    """Input schema for coverage estimation tool."""

    targeting: dict[str, Any] = Field(
        description="Targeting specification with demographics, interests, behaviors, etc."
    )
    channel: Optional[str] = Field(
        default=None,
        description="Specific channel to estimate (display, video, ctv, mobile_app)",
    )
    total_impressions: Optional[int] = Field(
        default=10000000,
        ge=0,
        description="Total available impressions to estimate against",
    )


class CoverageEstimationTool(BaseTool):
    """Estimate audience coverage for targeting combinations.

    This tool estimates the reach and coverage percentage for a given
    targeting specification across inventory.
    """

    name: str = "estimate_audience_coverage"
    description: str = """Estimate audience coverage for a targeting specification.
    Returns estimated impressions, coverage percentage, and factors that
    may limit reach. Use this to understand potential scale before committing
    to a targeting strategy."""
    args_schema: Type[BaseModel] = CoverageEstimationInput

    def _run(
        self,
        targeting: dict[str, Any],
        channel: Optional[str] = None,
        total_impressions: Optional[int] = 10000000,
    ) -> str:
        """Execute the coverage estimation."""
        return asyncio.run(
            self._arun(targeting, channel, total_impressions)
        )

    async def _arun(
        self,
        targeting: dict[str, Any],
        channel: Optional[str] = None,
        total_impressions: Optional[int] = 10000000,
    ) -> str:
        """Async implementation of coverage estimation."""
        if not targeting:
            return "Error: No targeting specification provided."

        # Calculate coverage based on targeting complexity
        estimates = self._calculate_coverage(
            targeting,
            channel,
            total_impressions or 10000000,
        )

        return self._format_results(estimates, targeting, channel)

    def _calculate_coverage(
        self,
        targeting: dict[str, Any],
        channel: Optional[str],
        total_impressions: int,
    ) -> list[CoverageEstimate]:
        """Calculate coverage estimates for targeting."""
        estimates = []

        # Base coverage factors by targeting type
        coverage_factors = {
            "demographics": 0.75,  # 75% of inventory has demo data
            "interests": 0.90,  # 90% has contextual signals
            "behaviors": 0.40,  # 40% has behavioral data
            "geography": 0.95,  # 95% has geo data
            "device": 0.98,  # 98% has device data
            "time_of_day": 1.0,  # 100% supports dayparting
        }

        # Channel-specific modifiers
        channel_modifiers = {
            "display": 1.0,
            "video": 0.85,  # Less video inventory
            "ctv": 0.60,  # CTV is more limited
            "mobile_app": 0.70,  # App inventory varies
        }

        # Calculate base coverage
        active_factors = []
        limiting = []

        for targeting_type, factor in coverage_factors.items():
            if targeting_type in targeting and targeting[targeting_type]:
                active_factors.append(factor)
                if factor < 0.7:
                    limiting.append(f"{targeting_type} ({factor*100:.0f}% coverage)")

        if not active_factors:
            # No specific targeting = 100% coverage
            base_coverage = 1.0
        else:
            # Multiply factors (assuming independence)
            base_coverage = 1.0
            for factor in active_factors:
                base_coverage *= factor

        # Apply channel modifier
        if channel and channel in channel_modifiers:
            base_coverage *= channel_modifiers[channel]
            channels = [channel]
        else:
            channels = list(channel_modifiers.keys())

        # Generate estimates per channel
        for ch in channels:
            ch_modifier = channel_modifiers.get(ch, 1.0)
            ch_coverage = base_coverage * ch_modifier

            # Determine confidence based on complexity
            if len(active_factors) <= 1:
                confidence = "high"
            elif len(active_factors) <= 3:
                confidence = "medium"
            else:
                confidence = "low"

            estimates.append(
                CoverageEstimate(
                    targeting_key=f"{ch}_{hash(str(targeting)) % 10000:04d}",
                    estimated_impressions=int(total_impressions * ch_coverage),
                    coverage_percentage=ch_coverage * 100,
                    confidence_level=confidence,
                    limiting_factors=limiting if ch_coverage < 0.5 else [],
                    channel=ch,
                )
            )

        return estimates

    def _format_results(
        self,
        estimates: list[CoverageEstimate],
        targeting: dict[str, Any],
        channel: Optional[str],
    ) -> str:
        """Format estimates as human-readable output."""
        output = "## Audience Coverage Estimates\n\n"

        # Targeting summary
        output += "**Targeting Applied:**\n"
        for key, value in targeting.items():
            if value:
                if isinstance(value, list):
                    output += f"   {key}: {', '.join(str(v) for v in value)}\n"
                elif isinstance(value, dict):
                    output += f"   {key}: {value}\n"
                else:
                    output += f"   {key}: {value}\n"
        output += "\n"

        # Coverage by channel
        output += "**Coverage by Channel:**\n\n"

        for estimate in sorted(estimates, key=lambda x: x.coverage_percentage, reverse=True):
            ch = estimate.channel or "unknown"
            coverage = estimate.coverage_percentage
            impressions = estimate.estimated_impressions
            confidence = estimate.confidence_level

            # Visual indicator
            if coverage >= 70:
                indicator = "[HIGH]"
            elif coverage >= 40:
                indicator = "[MED]"
            else:
                indicator = "[LOW]"

            output += f"**{ch.upper()}** {indicator}\n"
            output += f"   Coverage: {coverage:.1f}%\n"
            output += f"   Est. Impressions: {impressions:,}\n"
            output += f"   Confidence: {confidence}\n"

            if estimate.limiting_factors:
                output += f"   Limiting Factors: {', '.join(estimate.limiting_factors)}\n"

            output += "\n"

        # Overall summary
        if estimates:
            avg_coverage = sum(e.coverage_percentage for e in estimates) / len(estimates)
            total_reach = sum(e.estimated_impressions for e in estimates)

            output += "---\n"
            output += f"**Overall:** Avg coverage {avg_coverage:.1f}%, "
            output += f"total reach {total_reach:,} impressions\n\n"

            # Recommendations
            output += "**Recommendations:**\n"

            if avg_coverage >= 70:
                output += "- Coverage is strong - targeting is scalable\n"
            elif avg_coverage >= 40:
                output += "- Coverage is moderate - consider broadening targeting for more scale\n"
            else:
                output += "- Coverage is limited - review targeting constraints\n"

            # Find limiting factors
            all_limiting = set()
            for e in estimates:
                all_limiting.update(e.limiting_factors)

            if all_limiting:
                output += f"- Main constraints: {', '.join(all_limiting)}\n"

        return output
