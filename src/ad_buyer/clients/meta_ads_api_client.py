# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads direct Graph API client — reach estimates only.

Used in the research phase to estimate audience reach + CPM before booking.
All other operations (booking, reporting, lifecycle) use MetaAdsCLIClient.
"""

import json

import httpx


class MetaAdsAPIClient:
    """Direct Graph API httpx client scoped to reach estimation.

    The Meta Ads CLI does not expose a reach-estimate command, so this
    client calls graph.facebook.com/v{version}/{account}/reachestimate
    directly using the same system user access token.
    """

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        api_version: str = "v21.0",
    ):
        self._token = access_token
        self._account_id = (
            ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"
        )
        self._base = f"https://graph.facebook.com/{api_version}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        all_params = {"access_token": self._token, **(params or {})}
        r = httpx.get(f"{self._base}/{path}", params=all_params, timeout=30.0)
        r.raise_for_status()
        return r.json()

    def get_reach_estimate(
        self,
        targeting: dict,
        daily_budget: float,
        optimize_for: str = "REACH",
    ) -> dict:
        """Estimate reach for a targeting + daily budget combination.

        Args:
            targeting: Graph API targeting spec:
                {
                    "geo_locations": {"countries": ["US"]},
                    "age_min": 25, "age_max": 54
                }
            daily_budget: Daily budget in USD (converted to cents internally)
            optimize_for: REACH | IMPRESSIONS | LINK_CLICKS

        Returns:
            { "users_lower_bound": int, "users_upper_bound": int,
              "estimate_ready": bool }
        """
        return self._get(
            f"{self._account_id}/reachestimate",
            {
                "targeting_spec": json.dumps(targeting),
                "optimize_for": optimize_for,
                "daily_budget": int(daily_budget * 100),
            },
        )

    def get_ad_account(self) -> dict:
        """Get ad account metadata — name, currency, timezone."""
        return self._get(
            self._account_id,
            {"fields": "id,name,currency,timezone_name,account_status"},
        )
