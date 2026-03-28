import os
import sys
import json
from typing import Any, Optional

import requests

BASE_URL = os.getenv("SELLER_BASE_URL", "http://localhost:8001")
TARGET_PRICE = float(os.getenv("TARGET_PRICE", "29"))
STARTING_OFFER = float(os.getenv("STARTING_OFFER", "25"))
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "3"))


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def get_json(url: str) -> dict[str, Any]:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def find_best_package(media_kit: dict[str, Any]) -> Optional[dict[str, Any]]:
    packages = media_kit.get("all_packages", [])
    if not packages:
        return None

    scored: list[tuple[int, dict[str, Any]]] = []

    for pkg in packages:
        text_parts = [
            str(pkg.get("name", "")),
            str(pkg.get("description", "")),
            " ".join(pkg.get("cat", []) if isinstance(pkg.get("cat"), list) else []),
            " ".join(pkg.get("tags", []) if isinstance(pkg.get("tags"), list) else []),
        ]
        text = " ".join(text_parts).lower()

        score = 0
        if "sports" in text:
            score += 2
        if "ufc" in text:
            score += 5
        if "mma" in text:
            score += 3
        if "combat" in text:
            score += 2

        if score > 0:
            scored.append((score, pkg))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def try_create_proposal(pkg: dict[str, Any], offer_price: float) -> dict[str, Any]:
    """
    Tries a few common proposal endpoint patterns.
    Keep the first one that matches your Swagger.
    """
    package_name = pkg.get("name", "Unknown Package")
    product_id = (pkg.get("product_ids") or [None])[0]

    payloads = [
        # Pattern based on the repo request model
        {
            "endpoint": f"{BASE_URL}/proposals",
            "payload": {
                "product_id": product_id or "demo-product",
                "deal_type": "pmp",
                "price": offer_price,
                "impressions": 1000000,
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "buyer_id": "demo-buyer",
                "advertiser_id": "demo-advertiser",
                "agency_id": "demo-agency",
                "agent_url": "http://localhost/demo-buyer",
            },
        },
        # Fallback pattern if your local Swagger uses singular path/body
        {
            "endpoint": f"{BASE_URL}/proposal",
            "payload": {
                "package_name": package_name,
                "proposed_cpm": offer_price,
                "currency": "USD",
                "impressions": 1000000,
                "buyer_name": "Demo Buyer",
            },
        },
    ]

    last_error = None

    for candidate in payloads:
        try:
            print(f"\nTrying proposal endpoint: {candidate['endpoint']}")
            return post_json(candidate["endpoint"], candidate["payload"])
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not create proposal. Last error: {last_error}")


def try_negotiate(proposal_id: str, buyer_price: float) -> dict[str, Any]:
    payload = {
        "buyer_price": buyer_price,
        "buyer_tier": "advertiser",
        "agency_id": "demo-agency",
        "advertiser_id": "demo-advertiser",
    }

    endpoints = [
        f"{BASE_URL}/proposal/{proposal_id}/negotiation",
        f"{BASE_URL}/proposal/{proposal_id}/counter",
        f"{BASE_URL}/proposals/{proposal_id}/negotiation",
        f"{BASE_URL}/proposals/{proposal_id}/counter",
    ]

    last_error = None

    for endpoint in endpoints:
        try:
            print(f"Trying negotiation endpoint: {endpoint}")
            return post_json(endpoint, payload)
        except Exception as e:
            last_error = e

    raise RuntimeError(f"Could not negotiate proposal {proposal_id}. Last error: {last_error}")


def extract_proposal_id(response: dict[str, Any]) -> Optional[str]:
    for key in ["proposal_id", "id"]:
        if response.get(key):
            return str(response[key])
    return None


def extract_price(response: dict[str, Any]) -> Optional[float]:
    for key in ["final_price", "accepted_price", "counter_offer", "counter_price", "price"]:
        if response.get(key) is not None:
            try:
                return float(response[key])
            except Exception:
                pass
    return None


def extract_status(response: dict[str, Any]) -> str:
    return str(response.get("status", response.get("recommendation", "unknown"))).lower()


def main() -> None:
    print(f"Seller URL: {BASE_URL}")
    print("Step 1: Fetching media kit...")
    media_kit = get_json(f"{BASE_URL}/media-kit")

    packages = media_kit.get("all_packages", [])
    print(f"Packages found: {len(packages)}")

    chosen = find_best_package(media_kit)
    if not chosen:
        print("No sports/UFC package found.")
        sys.exit(1)

    print("\nStep 2: Best package selected")
    print(pretty(chosen))

    print(f"\nStep 3: Creating proposal with opening offer ${STARTING_OFFER:.2f} CPM")
    proposal_resp = try_create_proposal(chosen, STARTING_OFFER)
    print(pretty(proposal_resp))

    proposal_id = extract_proposal_id(proposal_resp)
    if not proposal_id:
        print("No proposal_id returned. Check your Swagger response shape.")
        sys.exit(1)

    status = extract_status(proposal_resp)
    price = extract_price(proposal_resp)

    if status in {"accepted", "approved"}:
        print(f"\nDeal accepted immediately at ${price:.2f}" if price else "\nDeal accepted immediately.")
        return

    current_offer = TARGET_PRICE

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\nStep 4: Negotiation round {round_num}")
        print(f"Submitting buyer counter-offer: ${current_offer:.2f}")

        neg_resp = try_negotiate(proposal_id, current_offer)
        print(pretty(neg_resp))

        status = extract_status(neg_resp)
        price = extract_price(neg_resp)

        if status in {"accepted", "approved"}:
            print(f"\nSuccess: deal accepted at ${price:.2f}" if price else "\nSuccess: deal accepted.")
            return

        if price is not None:
            print(f"Seller responded around: ${price:.2f}")

    print("\nNo agreement reached within max rounds.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print("\nHTTP error:")
        if e.response is not None:
            print(f"Status: {e.response.status_code}")
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text)
        else:
            print(str(e))
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)