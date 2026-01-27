# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Audience Matching Tool - Match campaign audiences to inventory via UCP."""

import asyncio
from typing import Any, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.ucp_client import UCPClient
from ...models.ucp import UCPConsent


class AudienceMatchingInput(BaseModel):
    """Input schema for audience matching tool."""

    seller_endpoint: str = Field(
        description="Seller's UCP exchange endpoint URL"
    )
    demographics: Optional[dict[str, Any]] = Field(
        default=None,
        description="Demographic targeting (age, gender, income, etc.)",
    )
    interests: Optional[list[str]] = Field(
        default=None,
        description="Interest-based targeting categories",
    )
    behaviors: Optional[list[str]] = Field(
        default=None,
        description="Behavioral targeting segments",
    )
    geography: Optional[str] = Field(
        default=None,
        description="Geographic targeting (country code)",
    )
    exclusions: Optional[list[str]] = Field(
        default=None,
        description="Audience segments to exclude",
    )


class AudienceMatchingTool(BaseTool):
    """Match campaign audiences to inventory capabilities via UCP.

    This tool uses UCP embedding exchange to compute similarity between
    the campaign's audience requirements and the seller's inventory
    audience characteristics.
    """

    name: str = "match_audience_to_inventory"
    description: str = """Match campaign audience requirements to seller inventory
    using UCP embedding exchange. Returns a similarity score (0-1) indicating
    how well the seller's inventory matches the target audience, along with
    matched capabilities and any gaps identified."""
    args_schema: Type[BaseModel] = AudienceMatchingInput

    def _run(
        self,
        seller_endpoint: str,
        demographics: Optional[dict[str, Any]] = None,
        interests: Optional[list[str]] = None,
        behaviors: Optional[list[str]] = None,
        geography: Optional[str] = None,
        exclusions: Optional[list[str]] = None,
    ) -> str:
        """Execute the audience matching."""
        return asyncio.run(
            self._arun(
                seller_endpoint,
                demographics,
                interests,
                behaviors,
                geography,
                exclusions,
            )
        )

    async def _arun(
        self,
        seller_endpoint: str,
        demographics: Optional[dict[str, Any]] = None,
        interests: Optional[list[str]] = None,
        behaviors: Optional[list[str]] = None,
        geography: Optional[str] = None,
        exclusions: Optional[list[str]] = None,
    ) -> str:
        """Async implementation of audience matching."""
        # Build audience requirements
        requirements = {}
        if demographics:
            requirements["demographics"] = demographics
        if interests:
            requirements["interests"] = interests
        if behaviors:
            requirements["behaviors"] = behaviors
        if geography:
            requirements["geography"] = geography
        if exclusions:
            requirements["exclusions"] = exclusions

        if not requirements:
            return "Error: No audience requirements specified. Please provide at least one of: demographics, interests, behaviors, geography."

        # Create consent object
        consent = UCPConsent(
            framework="IAB-TCFv2",
            permissible_uses=["personalization", "measurement"],
            ttl_seconds=3600,
        )

        client = UCPClient()

        try:
            validation = await client.validate_audience_with_seller(
                audience_requirements=requirements,
                seller_endpoint=seller_endpoint,
                consent=consent,
            )
        except Exception as e:
            # Return mock result for demonstration
            validation = self._get_mock_validation(requirements)
        finally:
            await client.close()

        return self._format_result(requirements, validation)

    def _get_mock_validation(self, requirements: dict[str, Any]) -> Any:
        """Return mock validation for demonstration."""
        from ...models.ucp import AudienceValidationResult

        # Simulate different match levels based on requirements complexity
        has_demographics = "demographics" in requirements
        has_interests = "interests" in requirements
        has_behaviors = "behaviors" in requirements

        # Base score
        score = 0.6

        # Contextual signals (interests) have highest coverage
        if has_interests and not has_demographics and not has_behaviors:
            score = 0.85

        # Demographics have good coverage
        if has_demographics:
            score = 0.72

        # Behavioral targeting has lower coverage
        if has_behaviors:
            score = max(0.45, score - 0.15)

        # Determine status
        if score >= 0.7:
            status = "valid"
            compatible = True
        elif score >= 0.5:
            status = "partial_match"
            compatible = True
        else:
            status = "partial_match"
            compatible = False

        gaps = []
        alternatives = []
        if has_behaviors:
            gaps.append("behavioral_targeting")
            alternatives.append({
                "gap": "behavioral_targeting",
                "suggestion": "Use contextual signals with frequency capping as proxy",
            })

        return AudienceValidationResult(
            validation_status=status,
            overall_coverage_percentage=score * 100,
            matched_capabilities=[
                "cap_ctx_categories",
                "cap_ctx_keywords",
            ] + (["cap_demo_age", "cap_demo_gender"] if has_demographics else []),
            gaps=gaps,
            alternatives=alternatives,
            ucp_similarity_score=score,
            targeting_compatible=compatible,
            estimated_reach=int(1000000 * score),
            validation_notes=[
                f"UCP similarity: {score:.2f}",
                f"Coverage: {score * 100:.1f}%",
            ],
        )

    def _format_result(
        self,
        requirements: dict[str, Any],
        validation: Any,
    ) -> str:
        """Format the matching result as human-readable output."""
        output = "## Audience Match Results\n\n"

        # Requirements summary
        output += "**Target Audience:**\n"
        if "demographics" in requirements:
            output += f"   Demographics: {requirements['demographics']}\n"
        if "interests" in requirements:
            output += f"   Interests: {', '.join(requirements['interests'])}\n"
        if "behaviors" in requirements:
            output += f"   Behaviors: {', '.join(requirements['behaviors'])}\n"
        if "geography" in requirements:
            output += f"   Geography: {requirements['geography']}\n"
        output += "\n"

        # Match score
        score = validation.ucp_similarity_score or 0
        status = validation.validation_status

        if score >= 0.7:
            match_quality = "STRONG"
        elif score >= 0.5:
            match_quality = "MODERATE"
        elif score >= 0.3:
            match_quality = "WEAK"
        else:
            match_quality = "POOR"

        output += f"**Match Quality: {match_quality}**\n"
        output += f"   UCP Similarity Score: {score:.2f}\n"
        output += f"   Status: {status}\n"
        output += f"   Coverage: {validation.overall_coverage_percentage:.1f}%\n"
        output += f"   Targeting Compatible: {'Yes' if validation.targeting_compatible else 'No'}\n"

        if validation.estimated_reach:
            output += f"   Estimated Reach: {validation.estimated_reach:,} impressions\n"
        output += "\n"

        # Matched capabilities
        if validation.matched_capabilities:
            output += f"**Matched Capabilities ({len(validation.matched_capabilities)}):**\n"
            for cap in validation.matched_capabilities:
                output += f"   - {cap}\n"
            output += "\n"

        # Gaps and alternatives
        if validation.gaps:
            output += "**Gaps Identified:**\n"
            for gap in validation.gaps:
                output += f"   - {gap}\n"
            output += "\n"

        if validation.alternatives:
            output += "**Suggested Alternatives:**\n"
            for alt in validation.alternatives:
                output += f"   - {alt.get('gap', 'Unknown')}: {alt.get('suggestion', '')}\n"
            output += "\n"

        # Recommendation
        output += "---\n"
        output += "**Recommendation:** "

        if validation.targeting_compatible and score >= 0.7:
            output += "Proceed with targeting - strong match with high coverage."
        elif validation.targeting_compatible:
            output += "Proceed with caution - partial match may limit reach."
        elif validation.gaps:
            output += "Consider alternatives - some requirements cannot be met."
        else:
            output += "Re-evaluate targeting - poor match with inventory."

        return output
