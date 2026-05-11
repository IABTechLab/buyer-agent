# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads inventory research tool — reach estimates via Graph API."""

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.meta_ads_api_client import MetaAdsAPIClient
from ...config.settings import settings

_CHANNEL_PLACEMENTS: dict[str, list[str]] = {
    "social":      ["FACEBOOK_FEED", "INSTAGRAM_FEED", "INSTAGRAM_REELS", "FACEBOOK_VIDEO_FEEDS"],
    "branding":    ["FACEBOOK_VIDEO_FEEDS", "INSTAGRAM_REELS"],
    "performance": ["FACEBOOK_FEED", "AUDIENCE_NETWORK_REWARDED_VIDEO"],
    "ctv":         ["INSTAGRAM_REELS"],
}


class MetaInventoryInput(BaseModel):
    channel: str = Field(default="social", description="IAB channel: social, branding, performance, ctv")
    budget: float = Field(..., description="Total budget in USD")
    objectives: list[str] = Field(default=["brand_awareness"], description="Campaign objectives")
    demographics: dict = Field(default_factory=dict, description="Age/gender targeting")
    interests: list[str] = Field(default_factory=list, description="Interest keywords")
    geo_locations: list[str] = Field(default=["US"], description="Target country codes")


class MetaInventoryTool(BaseTool):
    """Discover Meta Ads placements with reach estimates via Graph API."""

    name: str = "search_meta_placements"
    description: str = """Search Meta Ads inventory (Facebook, Instagram, Audience Network).
Returns available placements with estimated reach, CPM, and audience size.
Use for campaigns targeting social media audiences.

Args:
    channel: IAB channel (social, branding, performance, ctv)
    budget: Total budget in USD
    objectives: Campaign objectives list
    demographics: Age/gender dict e.g. {"age_min": 25, "age_max": 54}
    interests: Interest keywords list
    geo_locations: ISO country codes e.g. ["US", "CA"]

Returns: Available Meta placements with reach estimates and CPM."""

    args_schema: type[BaseModel] = MetaInventoryInput

    def _run(
        self,
        channel: str = "social",
        budget: float = 0,
        objectives: list[str] | None = None,
        demographics: dict | None = None,
        interests: list[str] | None = None,
        geo_locations: list[str] | None = None,
    ) -> str:
        if not settings.meta_access_token or not settings.meta_ad_account_id:
            return "Meta not configured. Set META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in .env"

        placements = _CHANNEL_PLACEMENTS.get(channel, _CHANNEL_PLACEMENTS["social"])
        demographics = demographics or {}
        geo_locations = geo_locations or ["US"]

        targeting: dict = {"geo_locations": {"countries": geo_locations}}
        if demographics.get("age_min"):
            targeting["age_min"] = int(demographics["age_min"])
        if demographics.get("age_max"):
            targeting["age_max"] = int(demographics["age_max"])

        client = MetaAdsAPIClient(
            access_token=settings.meta_access_token,
            ad_account_id=settings.meta_ad_account_id,
            api_version=settings.meta_api_version,
        )

        try:
            reach_data = client.get_reach_estimate(
                targeting=targeting,
                daily_budget=budget / 30,
            )
            lower = reach_data.get("users_lower_bound", 0)
            upper = reach_data.get("users_upper_bound", 0)
            estimated_reach = (lower + upper) // 2 or int(budget * 100)
        except Exception as e:
            estimated_reach = int(budget * 100)
            return self._format(placements, channel, budget, estimated_reach,
                                note=f"(estimated — API error: {e})")

        return self._format(placements, channel, budget, estimated_reach)

    def _format(
        self,
        placements: list[str],
        channel: str,
        budget: float,
        estimated_reach: int,
        note: str = "",
    ) -> str:
        estimated_impressions = int(estimated_reach * 2.5)
        estimated_cpm = (budget / estimated_impressions * 1000) if estimated_impressions else 4.0
        per_budget = budget / len(placements)
        per_impressions = estimated_impressions // len(placements)

        out = f"Found {len(placements)} Meta placements for {channel} {note}:\n\n"
        for i, p in enumerate(placements, 1):
            pid = f"meta:{p.lower().replace('_', '-')}"
            fmt = "video" if "VIDEO" in p or "REELS" in p else "display"
            out += f"""{i}. {p.replace('_', ' ').title()}
   Product ID: {pid}
   Publisher: Meta (Facebook/Instagram)
   Channel: {channel}
   Format: {fmt}
   Estimated Reach: {estimated_reach // len(placements):,} users
   Estimated Impressions: {per_impressions:,}
   Estimated CPM: ${estimated_cpm:.2f}
   Estimated Cost: ${per_budget:,.2f}
   ---
"""
        return out
