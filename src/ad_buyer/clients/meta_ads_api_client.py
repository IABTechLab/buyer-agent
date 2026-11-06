# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Meta Ads reach-estimate client — Graph API reach estimates only.

Used in the research phase to estimate audience reach + CPM before booking.
All other operations (booking, reporting, lifecycle) use MetaAdsClient
(meta_ads_client.py).
"""

import json

import httpx


class MetaAdsAPIClient:
    """Graph API httpx client scoped to reach estimation.

    Calls graph.facebook.com/v{version}/{account}/reachestimate
    directly using a system user access token.
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
        r = httpx.get(
            f"{self._base}/{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()

    def get_reach_estimate(self, targeting: dict) -> dict:
        """Estimate audience reach for a targeting spec.

        Args:
            targeting: Graph API targeting spec:
                {
                    "geo_locations": {"countries": ["US"]},
                    "age_min": 25, "age_max": 54
                }

        Returns:
            { "users_lower_bound": int, "users_upper_bound": int,
              "estimate_ready": bool }
        """
        result = self._get(
            f"{self._account_id}/reachestimate",
            {"targeting_spec": json.dumps(targeting)},
        )
        return result.get("data", result)

    def get_ad_account(self) -> dict:
        """Get ad account metadata — name, currency, timezone."""
        return self._get(
            self._account_id,
            {"fields": "id,name,currency,timezone_name,account_status"},
        )
