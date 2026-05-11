# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads reporting tool — campaign insights via meta ads CLI."""

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ...clients.meta_ads_cli_client import MetaAdsCLIClient, MetaAuthError, MetaAPIError
from ...config.settings import settings


class MetaReportInput(BaseModel):
    campaign_ids: list[str] = Field(..., description="Meta campaign IDs to report on")
    date_preset: str = Field(
        default="last_30d",
        description="last_7d | last_14d | last_30d | last_90d",
    )


class MetaReportingTool(BaseTool):
    """Pull real performance data from Meta Ads via CLI insights command."""

    name: str = "get_meta_campaign_report"
    description: str = """Retrieve real performance data from Meta Ads Manager.
Returns spend, impressions, reach, frequency, CTR, CPM per campaign.

Args:
    campaign_ids: List of Meta campaign IDs (from booked_lines.order_id)
    date_preset: Reporting window (last_7d, last_14d, last_30d, last_90d)"""

    args_schema: type[BaseModel] = MetaReportInput

    def _run(self, campaign_ids: list[str], date_preset: str = "last_30d") -> str:
        if not settings.meta_access_token or not settings.meta_ad_account_id:
            return "Meta not configured. Set META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in .env"
        if not settings.meta_page_id:
            return "META_PAGE_ID not set in .env"

        cli = MetaAdsCLIClient(
            access_token=settings.meta_access_token,
            ad_account_id=settings.meta_ad_account_id,
            page_id=settings.meta_page_id,
            api_version=settings.meta_api_version,
        )

        output = f"Meta Ads Performance Report — {len(campaign_ids)} campaign(s)\n{'='*50}\n\n"

        for campaign_id in campaign_ids:
            try:
                rows = cli.get_insights(campaign_id, date_preset=date_preset)
                for row in rows:
                    spend       = float(row.get("spend", 0))
                    impressions = int(row.get("impressions", 0))
                    reach       = int(row.get("reach", 0))
                    frequency   = float(row.get("frequency", 0))
                    ctr         = float(row.get("ctr", 0))
                    cpm         = float(row.get("cpm", 0))
                    output += f"""Campaign: {row.get('campaign_name', campaign_id)}
  Spend:       ${spend:,.2f}
  Impressions: {impressions:,}
  Reach:       {reach:,} unique users
  Frequency:   {frequency:.2f}x
  CTR:         {ctr:.3f}%
  CPM:         ${cpm:.2f}
  ---
"""
            except MetaAuthError as e:
                output += f"Campaign {campaign_id}: Auth error — {e}\n"
            except MetaAPIError as e:
                output += f"Campaign {campaign_id}: API error — {e}\n"
            except Exception as e:
                output += f"Campaign {campaign_id}: Error — {e}\n"

        return output
