# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads client — wraps the meta_ads Python API (from meta-ads-cli package).

Uses meta_ads.api.MetaAdsAPI directly (no subprocess) for campaign management.
Auth: META_ACCESS_TOKEN / META_AD_ACCOUNT_ID / META_PAGE_ID env vars.
All created resources are PAUSED by default.

Install: pip install meta-ads-cli  (or uv pip install -e ".[meta]")
"""

from typing import Any


class MetaAuthError(Exception):
    """Raised when the Meta API rejects the access token."""


class MetaAPIError(Exception):
    """Raised when the Meta API returns a 4xx/5xx error."""


class MetaAdsCLIClient:
    """Meta Ads client using the meta_ads Python API directly.

    Wraps meta_ads.api.MetaAdsAPI which calls graph.facebook.com under the hood.
    All write operations create resources in PAUSED state by default.
    """

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        page_id: str,
        api_version: str = "v21.0",
    ):
        self._access_token  = access_token
        # meta_ads expects numeric account ID without act_ prefix
        self._ad_account_id = ad_account_id.replace("act_", "")
        self._page_id       = page_id
        self._api_version   = api_version

    def _api(self, dry_run: bool = False):
        """Return a MetaAdsAPI instance."""
        try:
            from meta_ads.api import MetaAdsAPI, MetaAPIError as _MetaAPIError
        except ImportError:
            raise ImportError(
                "meta-ads-cli is not installed. "
                "Install with: pip install -e '.[meta]' or uv pip install meta-ads-cli"
            )
        return MetaAdsAPI(
            access_token=self._access_token,
            ad_account_id=self._ad_account_id,
            page_id=self._page_id,
            api_version=self._api_version,
            dry_run=dry_run,
        )

    def _wrap(self, fn, *args, **kwargs) -> Any:
        """Call fn, translating meta_ads errors to our exception types."""
        try:
            from meta_ads.api import MetaAPIError as _MetaAPIError
        except ImportError:
            raise ImportError("meta-ads-cli not installed")
        try:
            return fn(*args, **kwargs)
        except _MetaAPIError as e:
            code = getattr(e, "error_code", None)
            msg  = str(e)
            if code in (190, 102, 32):          # token expired / invalid
                raise MetaAuthError(f"Meta auth error (code {code}): {msg}") from e
            raise MetaAPIError(f"Meta API error (code {code}): {msg}") from e

    # -------------------------------------------------------------------------
    # Campaign
    # -------------------------------------------------------------------------

    def create_campaign(
        self,
        name: str,
        objective: str,
        daily_budget_cents: int,
    ) -> dict[str, Any]:
        """Create a campaign in PAUSED state. Returns dict with 'id' key."""
        api = self._api()
        campaign_id = self._wrap(
            api.create_campaign,
            name=name,
            objective=objective,
            status="PAUSED",
        )
        return {"id": campaign_id}

    def update_campaign(self, campaign_id: str, status: str) -> dict[str, Any]:
        """Update campaign status. status: ACTIVE | PAUSED | DELETED"""
        api = self._api()
        self._wrap(api.update_status, campaign_id, status)
        return {"id": campaign_id, "status": status}

    def list_campaigns(self) -> list[dict[str, Any]]:
        """List campaigns for the ad account via Graph API."""
        import httpx
        r = httpx.get(
            f"https://graph.facebook.com/{self._api_version}/act_{self._ad_account_id}/campaigns",
            params={
                "fields": "id,name,status,objective",
                "access_token": self._access_token,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", [])

    # -------------------------------------------------------------------------
    # Ad Set
    # -------------------------------------------------------------------------

    def create_adset(
        self,
        campaign_id: str,
        name: str,
        optimization_goal: str,
        billing_event: str,
        bid_amount_cents: int,
        targeting_countries: list[str],
    ) -> dict[str, Any]:
        """Create an ad set under a campaign in PAUSED state."""
        api = self._api()
        targeting = {
            "age_min": 18,
            "age_max": 65,
            "countries": targeting_countries,
            "platforms": ["facebook", "instagram"],
        }
        ad_set_id = self._wrap(
            api.create_ad_set,
            name=name,
            campaign_id=campaign_id,
            daily_budget=bid_amount_cents,
            targeting=targeting,
            optimization_goal=optimization_goal,
            billing_event=billing_event,
            status="PAUSED",
        )
        return {"id": ad_set_id}

    # -------------------------------------------------------------------------
    # Creative
    # -------------------------------------------------------------------------

    def create_creative(
        self,
        name: str,
        body: str,
        title: str,
        link_url: str,
        call_to_action: str = "LEARN_MORE",
        image_path: str | None = None,
    ) -> dict[str, Any]:
        """Create an ad creative. image_path is optional."""
        api = self._api()

        image_hash: str = ""
        if image_path:
            import pathlib
            image_hash = self._wrap(api.upload_image, pathlib.Path(image_path))

        creative_id = self._wrap(
            api.create_ad_creative,
            name=name,
            image_hash=image_hash,
            primary_text=body,
            headline=title,
            description="",
            link=link_url,
            cta=call_to_action,
        )
        return {"id": creative_id}

    # -------------------------------------------------------------------------
    # Ad
    # -------------------------------------------------------------------------

    def create_ad(self, adset_id: str, name: str, creative_id: str) -> dict[str, Any]:
        """Create an ad linking a creative to an ad set."""
        api = self._api()
        ad_id = self._wrap(
            api.create_ad,
            name=name,
            ad_set_id=adset_id,
            creative_id=creative_id,
            status="PAUSED",
        )
        return {"id": ad_id}

    def update_ad(self, ad_id: str, status: str) -> dict[str, Any]:
        api = self._api()
        self._wrap(api.update_status, ad_id, status)
        return {"id": ad_id, "status": status}

    # -------------------------------------------------------------------------
    # Insights (Graph API — meta_ads package has no insights method)
    # -------------------------------------------------------------------------

    def get_insights(
        self,
        campaign_id: str,
        date_preset: str = "last_30d",
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get performance insights via Graph API."""
        import httpx
        default_fields = [
            "campaign_name", "spend", "impressions", "reach",
            "frequency", "clicks", "ctr", "cpm",
        ]
        r = httpx.get(
            f"https://graph.facebook.com/{self._api_version}/{campaign_id}/insights",
            params={
                "fields": ",".join(fields or default_fields),
                "date_preset": date_preset,
                "level": "campaign",
                "access_token": self._access_token,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return data if data else [{}]

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def pause(self, campaign_id: str) -> dict[str, Any]:
        return self.update_campaign(campaign_id, "PAUSED")

    def activate(self, campaign_id: str) -> dict[str, Any]:
        return self.update_campaign(campaign_id, "ACTIVE")

    def delete(self, campaign_id: str) -> dict[str, Any]:
        return self.update_campaign(campaign_id, "DELETED")
