#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulate a buy: sports -> UFC -> negotiate at 10% discount.

Flow:
  1. Buyer searches for sports packages (via buyer API on port 8000).
  2. Buyer searches for UFC packages (via buyer API on port 8000).
  3. Buyer creates a proposal and negotiates at 10% off list price (via seller on port 8001).

Usage:
  BUYER_URL=http://localhost:8000 SELLER_URL=http://localhost:8001 python scripts/simulate_sports_ufc_negotiation.py
  DEMO_DELAY_SECONDS=3  # 3s pause between agent turns (default). Use 0 to disable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Optional

import requests

BUYER_URL = os.getenv("BUYER_URL", "http://localhost:8000")
SELLER_URL = os.getenv("SELLER_URL", "http://localhost:8001")
MAX_NEGOTIATION_ROUNDS = int(os.getenv("MAX_NEGOTIATION_ROUNDS", "5"))
# Optional: API keys (set to match seller's API_KEY / buyer's API_KEY if they require auth)
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "").strip()
BUYER_API_KEY = os.getenv("BUYER_API_KEY", "").strip()
# Optional: set if your seller uses different paths (e.g. SELLER_PROPOSAL_ENDPOINT=http://localhost:8001/api/v2/proposals)
SELLER_PROPOSAL_ENDPOINT = os.getenv("SELLER_PROPOSAL_ENDPOINT", "").strip()
SELLER_NEGOTIATION_PATH = os.getenv("SELLER_NEGOTIATION_PATH", "").strip()  # e.g. /proposals/{proposal_id}/counter
# If set, skip proposal/negotiation and print payloads (useful when seller returns 500 or needs different product_id)
SIMULATE_DRY_RUN = os.getenv("SIMULATE_DRY_RUN", "").lower() in ("1", "true", "yes")
# Delay in seconds between agent turns (for demos). Set DEMO_DELAY_SECONDS=0 to disable.
DEMO_DELAY_SECONDS = float(os.getenv("DEMO_DELAY_SECONDS", "3"))


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _delay() -> None:
    """Pause between agent turns for demo (DEMO_DELAY_SECONDS)."""
    if DEMO_DELAY_SECONDS > 0:
        time.sleep(DEMO_DELAY_SECONDS)


def _convo(agent: str, message: str, sublines: Optional[list[str]] = None) -> None:
    """Print one agent's line in the conversation, then delay."""
    label = f"  [{agent}]"
    print(f"{label} {message}")
    if sublines:
        for line in sublines:
            print(f"       {line}")
    print()
    _delay()


def _seller_headers() -> dict[str, str]:
    """Headers for seller requests (X-API-Key if SELLER_API_KEY is set)."""
    if SELLER_API_KEY:
        return {"X-API-Key": SELLER_API_KEY}
    return {}


def _buyer_headers() -> dict[str, str]:
    """Headers for buyer requests (X-API-Key if BUYER_API_KEY is set)."""
    if BUYER_API_KEY:
        return {"X-API-Key": BUYER_API_KEY}
    return {}


def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
    r = requests.get(url, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    r = requests.post(url, json=payload, timeout=30, **kwargs)
    r.raise_for_status()
    return r.json()


def search_via_buyer(query: str) -> list[dict[str, Any]]:
    """Search media kit via buyer agent; fall back to seller if buyer returns 404 or 401."""
    try:
        resp = post_json(
            f"{BUYER_URL}/media-kit/search",
            {"query": query},
            headers=_buyer_headers(),
        )
        return resp.get("packages", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 401):
            # Buyer may not expose /media-kit/search or may require auth; try seller directly
            try:
                resp = post_json(
                    f"{SELLER_URL}/media-kit/search",
                    {"query": query},
                    headers=_seller_headers(),
                )
                return resp.get("results", resp.get("packages", []))
            except Exception:
                pass
        raise


def parse_price_from_package(pkg: dict[str, Any]) -> float:
    """Extract numeric CPM from price_range or exact_price. Default 30.0."""
    exact = pkg.get("exact_price")
    if exact is not None:
        return float(exact)
    price_range = pkg.get("price_range") or ""
    # e.g. "$28-$42 CPM" or "$30 CPM"
    numbers = re.findall(r"[\d.]+", price_range)
    if numbers:
        return float(numbers[0])
    return 30.0


def get_seller_media_kit() -> dict[str, Any]:
    """Fetch full media kit from seller."""
    return get_json(f"{SELLER_URL}/media-kit", headers=_seller_headers())


def get_seller_product_ids() -> list[str]:
    """Fetch product IDs from seller GET /products (so proposal uses a valid product_id)."""
    try:
        data = get_json(f"{SELLER_URL}/products", headers=_seller_headers())
        products = data.get("products", data) if isinstance(data, dict) else data
        if isinstance(products, list):
            return [p.get("product_id", p.get("id", "")) for p in products if p.get("product_id") or p.get("id")]
    except Exception:
        pass
    return []


def discover_seller_paths() -> tuple[Optional[str], Optional[str]]:
    """Find proposal and negotiation paths from seller OpenAPI spec. Returns (proposal_path, negotiation_path)."""
    proposal_path: Optional[str] = None
    negotiation_path: Optional[str] = None
    try:
        data = get_json(f"{SELLER_URL}/openapi.json", headers=_seller_headers())
    except Exception:
        return None, None
    paths = data.get("paths", data) if isinstance(data, dict) else {}
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        path_lower = path.lower()
        has_post = "post" in methods
        if has_post and ("proposal" in path_lower or "quote" in path_lower):
            if "counter" in path_lower or "negotiation" in path_lower:
                if not negotiation_path:
                    negotiation_path = path
            else:
                if not proposal_path:
                    proposal_path = path
    return proposal_path, negotiation_path


def find_ufc_or_sports_package(media_kit: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pick best UFC, then sports, package from seller media kit."""
    packages = media_kit.get("all_packages", media_kit.get("packages", []))
    if not packages:
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for pkg in packages:
        text_parts = [
            str(pkg.get("name", "")),
            str(pkg.get("description", "")),
            " ".join(pkg.get("cat", []) or []),
            " ".join(pkg.get("tags", []) or []),
        ]
        text = " ".join(text_parts).lower()
        score = 0
        if "ufc" in text:
            score += 5
        if "mma" in text or "combat" in text:
            score += 3
        if "sports" in text:
            score += 2
        if score > 0:
            scored.append((score, pkg))
    if not scored:
        return packages[0]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def create_proposal(
    pkg: dict[str, Any],
    offer_price: float,
    proposal_endpoint: Optional[str] = None,
    seller_product_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a proposal/quote on the seller. Tries env/discovered endpoint then common patterns."""
    package_name = pkg.get("name", "Unknown Package")
    # Prefer a product_id from seller's GET /products so proposal is valid (avoids 422/404)
    if seller_product_ids:
        product_id = seller_product_ids[0]
    else:
        product_id = pkg.get("product_ids") or pkg.get("package_id")
        if isinstance(product_id, list):
            product_id = product_id[0] if product_id else "demo-product"
        elif not product_id:
            product_id = "demo-product"

    quote_payload = {
        "product_id": product_id,
        "deal_type": "PD",
        "impressions": 1_000_000,
        "flight_start": "2026-04-01",
        "flight_end": "2026-04-30",
        "target_cpm": offer_price,
        "buyer_identity": {
            "agency_id": "demo-agency",
            "advertiser_id": "demo-advertiser",
        },
        "agent_url": BUYER_URL,
    }
    generic_payload = {
        "product_id": product_id,
        "price": offer_price,
        "impressions": 1_000_000,
        "target_cpm": offer_price,
    }

    headers = _seller_headers()

    # 1) Explicit env endpoint
    if SELLER_PROPOSAL_ENDPOINT:
        try:
            return post_json(SELLER_PROPOSAL_ENDPOINT, quote_payload, headers=headers)
        except Exception as e:
            try:
                return post_json(SELLER_PROPOSAL_ENDPOINT, generic_payload, headers=headers)
            except Exception:
                raise RuntimeError(f"SELLER_PROPOSAL_ENDPOINT failed: {e}") from e

    # 2) Discovered path from OpenAPI
    if proposal_endpoint:
        try:
            return post_json(proposal_endpoint, quote_payload, headers=headers)
        except Exception:
            try:
                return post_json(proposal_endpoint, generic_payload, headers=headers)
            except Exception:
                pass

    # 3) Hardcoded candidates (seller POST /proposals: exact ProposalRequest fields only to avoid 422)
    proposals_payload = {
        "product_id": product_id,
        "deal_type": "PD",
        "price": offer_price,
        "impressions": 1_000_000,
        "start_date": "2026-04-01",
        "end_date": "2026-04-30",
        "buyer_id": "demo-buyer",
        "advertiser_id": "demo-advertiser",
        "agency_id": "demo-agency",
        "agent_url": BUYER_URL,
    }
    candidates = [
        {"endpoint": f"{SELLER_URL}/api/v1/quotes", "payload": quote_payload},
        {"endpoint": f"{SELLER_URL}/proposals", "payload": proposals_payload},
        {"endpoint": f"{SELLER_URL}/proposal", "payload": {"package_name": package_name, "proposed_cpm": offer_price, "currency": "USD", "impressions": 1_000_000, "buyer_name": "Demo Buyer"}},
    ]
    last_error = None
    errors: list[str] = []
    for c in candidates:
        try:
            return post_json(c["endpoint"], c["payload"], headers=headers)
        except requests.HTTPError as e:
            err_msg = f"{c['endpoint']}: {e.response.status_code if e.response is not None else '?'}"
            errors.append(err_msg)
            last_error = e
        except Exception as e:
            errors.append(f"{c['endpoint']}: {e}")
            last_error = e
    err_detail = "; ".join(errors) if errors else str(last_error)
    msg = f"Could not create proposal. Tried: {err_detail}"
    if "500" in err_detail:
        msg += " (Seller 500 may mean product_id is not in seller's product catalog; try SIMULATE_DRY_RUN=1 to see payload.)"
    raise RuntimeError(msg)


def negotiate_round(
    proposal_id: str,
    buyer_price: float,
    negotiation_path_template: Optional[str] = None,
) -> dict[str, Any]:
    """Send one negotiation counter to seller.

    Seller expects POST /proposals/{proposal_id}/counter with CounterOfferRequest:
    buyer_price (float, required), buyer_tier (str), agency_id, advertiser_id (optional).
    """
    # Payload matching seller CounterOfferRequest (buyer_price required)
    counter_payload: dict[str, Any] = {
        "buyer_price": float(buyer_price),
        "buyer_tier": "public",
    }
    payloads = [
        counter_payload,
        {**counter_payload, "agency_id": "demo-agency", "advertiser_id": "demo-advertiser"},
    ]
    urls: list[str] = []
    template = SELLER_NEGOTIATION_PATH or negotiation_path_template
    if template:
        path = template.replace("{proposal_id}", proposal_id)
        urls.append(f"{SELLER_URL.rstrip('/')}{path}" if not path.startswith("http") else path)
    if not urls:
        # Try seller's actual path first: POST /proposals/{proposal_id}/counter
        urls = [
            f"{SELLER_URL.rstrip('/')}/proposals/{proposal_id}/counter",
            f"{SELLER_URL}/api/v1/proposals/{proposal_id}/counter",
            f"{SELLER_URL}/proposal/{proposal_id}/counter",
            f"{SELLER_URL}/proposals/{proposal_id}/negotiation",
            f"{SELLER_URL}/proposal/{proposal_id}/negotiation",
        ]
    headers = _seller_headers()
    last_err = None
    last_response = None
    for url in urls:
        for payload in payloads:
            try:
                return post_json(url, payload, headers=headers)
            except requests.HTTPError as e:
                last_err = e
                if e.response is not None:
                    last_response = e.response
                    try:
                        body = e.response.json()
                        if e.response.status_code == 422 and isinstance(body, dict) and "detail" in body:
                            last_err = RuntimeError(
                                f"{e.response.status_code} {e.response.reason}: {body.get('detail')}"
                            )
                    except Exception:
                        pass
                continue
            except Exception as e:
                last_err = e
                continue
    msg = str(last_err)
    if last_response is not None and getattr(last_response, "text", None):
        msg += f" | Response: {last_response.text[:500]}"
    raise RuntimeError(f"Could not negotiate proposal {proposal_id}. Last error: {msg}")


def extract_proposal_id(response: dict[str, Any]) -> Optional[str]:
    for key in ("proposal_id", "quote_id", "id"):
        if response.get(key):
            return str(response[key])
    return None


def extract_price(response: dict[str, Any]) -> Optional[float]:
    for key in ("final_price", "accepted_price", "counter_offer", "counter_price", "seller_price", "price", "current_price"):
        if response.get(key) is not None:
            try:
                return float(response[key])
            except (TypeError, ValueError):
                pass
    return None


def extract_status(response: dict[str, Any]) -> str:
    return str(response.get("status", response.get("recommendation", response.get("action", "unknown")))).lower()


def main() -> None:
    print("=" * 60)
    print("SIMULATION: Sports -> UFC -> Negotiate at 10% discount")
    print("=" * 60)
    print(f"Buyer API:  {BUYER_URL}" + (" (with API key)" if BUYER_API_KEY else ""))
    print(f"Seller API: {SELLER_URL}" + (" (with API key)" if SELLER_API_KEY else ""))
    if DEMO_DELAY_SECONDS > 0:
        print(f"Demo delay: {DEMO_DELAY_SECONDS}s between turns (set DEMO_DELAY_SECONDS=0 to disable)")
    if not SELLER_API_KEY:
        print("  (Set SELLER_API_KEY to match seller's API_KEY in .env to authenticate.)")
    print()
    _delay()

    # Discover seller proposal/negotiation paths from OpenAPI if not set via env
    proposal_endpoint: Optional[str] = None
    negotiation_path_template: Optional[str] = None
    if not SELLER_PROPOSAL_ENDPOINT or not SELLER_NEGOTIATION_PATH:
        try:
            p_path, n_path = discover_seller_paths()
            if p_path:
                proposal_endpoint = f"{SELLER_URL.rstrip('/')}{p_path}"
            if n_path:
                negotiation_path_template = n_path.replace("{id}", "{proposal_id}")
        except Exception:
            pass

    # Step 1: Buyer searches for sports (via buyer agent)
    _convo("BUYER AGENT", "I'm looking for sports packages. Querying the seller's media kit...")
    try:
        sports_packages = search_via_buyer("sports")
        lines = [f"Found {len(sports_packages)} package(s) for 'sports':"]
        for i, p in enumerate(sports_packages[:5], 1):
            lines.append(f"  {i}. {p.get('name', 'Unknown')} - {p.get('price_range', '-')}")
        _convo("SELLER AGENT", "Here are the sports packages I have:", sublines=lines)
    except Exception as e:
        _convo("SELLER AGENT", f"Search failed: {e}")
        sports_packages = []

    # Step 2: Buyer searches for UFC (via buyer agent)
    _convo("BUYER AGENT", "I'm specifically interested in UFC inventory. Searching for UFC packages...")
    try:
        ufc_packages = search_via_buyer("UFC")
        lines = [f"Found {len(ufc_packages)} UFC package(s):"]
        for i, p in enumerate(ufc_packages[:5], 1):
            lines.append(f"  {i}. {p.get('name', 'Unknown')} - {p.get('price_range', '-')}")
        _convo("SELLER AGENT", "Here are my UFC packages:", sublines=lines)
    except Exception as e:
        _convo("SELLER AGENT", f"Search failed: {e}")
        ufc_packages = []

    # Step 3: Get seller media kit and pick package (UFC preferred, else sports)
    _convo("BUYER AGENT", "Requesting full media kit to confirm availability and list price...")
    try:
        media_kit = get_seller_media_kit()
        chosen = find_ufc_or_sports_package(media_kit)
    except Exception as e:
        _convo("SELLER AGENT", f"Error: {e}")
        sys.exit(1)

    if not chosen:
        _convo("SELLER AGENT", "No suitable package found.")
        sys.exit(1)

    list_price = parse_price_from_package(chosen)
    target_price = round(list_price * 0.90, 2)  # 10% discount
    pkg_name = chosen.get("name", "Unknown")
    _convo(
        "SELLER AGENT",
        f"Package: {pkg_name}. List price: ${list_price:.2f} CPM.",
        sublines=[f"Buyer target (10% off): ${target_price:.2f} CPM"],
    )

    if SIMULATE_DRY_RUN:
        product_id = chosen.get("product_ids") or chosen.get("package_id")
        if isinstance(product_id, list):
            product_id = product_id[0] if product_id else "demo-product"
        product_id = product_id or "demo-product"
        _convo("BUYER AGENT", "(DRY RUN) Would send proposal to seller...", sublines=[pretty({
            "product_id": product_id,
            "deal_type": "PD",
            "price": target_price,
            "impressions": 1_000_000,
        })])
        print("  Set SIMULATE_DRY_RUN=0 to run full flow.")
        return

    # Step 4: Create proposal with opening offer at 10% discount
    seller_product_ids = get_seller_product_ids()
    product_id_preview = f" (product_id: {seller_product_ids[0]})" if seller_product_ids else ""
    _convo(
        "BUYER AGENT",
        f"Submitting proposal for '{pkg_name}' at ${target_price:.2f} CPM (10% off list).{product_id_preview}",
    )
    try:
        proposal_resp = create_proposal(
            chosen, target_price, proposal_endpoint=proposal_endpoint, seller_product_ids=seller_product_ids or None
        )
    except Exception as e:
        _convo("SELLER AGENT", f"Proposal rejected: {e}")
        sys.exit(1)

    proposal_id = extract_proposal_id(proposal_resp)
    if not proposal_id:
        _convo("SELLER AGENT", f"No proposal_id in response: {pretty(proposal_resp)}")
        sys.exit(1)

    status = extract_status(proposal_resp)
    price = extract_price(proposal_resp)
    if status in ("accepted", "approved"):
        _convo("SELLER AGENT", f"Deal accepted immediately at ${price:.2f} CPM." if price else "Deal accepted.")
        return
    _convo(
        "SELLER AGENT",
        f"Proposal received (ID: {proposal_id}). " + (f"Counter: ${price:.2f} CPM." if price is not None else "Entering negotiation."),
    )

    # Step 5: Negotiation rounds: buyer holds for 10% off (target_price), max accept at list_price
    current_offer = target_price
    max_accept = list_price
    concession = (max_accept - target_price) / max(MAX_NEGOTIATION_ROUNDS, 1)

    for round_num in range(1, MAX_NEGOTIATION_ROUNDS + 1):
        _convo("BUYER AGENT", f"Round {round_num}: I'm offering ${current_offer:.2f} CPM.")
        try:
            neg_resp = negotiate_round(proposal_id, current_offer, negotiation_path_template=negotiation_path_template)
        except Exception as e:
            _convo("SELLER AGENT", f"Negotiation error: {e}")
            break

        status = extract_status(neg_resp)
        seller_price = extract_price(neg_resp)
        if seller_price is not None:
            _convo("SELLER AGENT", f"My counter: ${seller_price:.2f} CPM.")

        if status in ("accepted", "approved"):
            final = seller_price or current_offer
            print("  " + "=" * 56)
            _convo("SELLER AGENT", f"Deal accepted at ${final:.2f} CPM.")
            if final <= target_price:
                print(f"  >>> Buyer achieved 10% discount (target was ${target_price:.2f} CPM).")
            return

        # Next round: concede slightly toward list price, but never above max_accept
        current_offer = min(round(current_offer + concession, 2), max_accept)

    _convo("BUYER AGENT", "No agreement within max rounds.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print("\nHTTP error:")
        if e.response is not None:
            print(f"  Status: {e.response.status_code}")
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:500])
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
