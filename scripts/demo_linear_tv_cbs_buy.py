#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Demo: Linear TV buy across CBS Broadcast, CBS News, and Paramount cable nets.

Walks through a scripted buyer/seller conversation with real API calls to the
seller catalog (GET /products), optional media-kit search, proposal, and
negotiation. Prints each agent turn and pauses between turns for demos.

Usage:
  BUYER_URL=http://localhost:8000 SELLER_URL=http://localhost:8001 \\
    python3 scripts/demo_linear_tv_cbs_buy.py

  DEMO_DELAY_SECONDS=3   # default 3s between turns; use 0 to disable
  SELLER_API_KEY=...     # if your seller requires auth
  BUYER_API_KEY=...      # if your buyer requires auth for media-kit search
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Optional

import requests

BUYER_URL = os.getenv("BUYER_URL", "http://localhost:8000")
SELLER_URL = os.getenv("SELLER_URL", "http://localhost:8001")
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "").strip()
BUYER_API_KEY = os.getenv("BUYER_API_KEY", "").strip()
DEMO_DELAY_SECONDS = float(os.getenv("DEMO_DELAY_SECONDS", "3"))
SELLER_PROPOSAL_ENDPOINT = os.getenv("SELLER_PROPOSAL_ENDPOINT", "").strip()
SELLER_NEGOTIATION_PATH = os.getenv("SELLER_NEGOTIATION_PATH", "").strip()
# Skip live proposal/negotiation (still prints conversation)
DEMO_DRY_RUN = os.getenv("DEMO_DRY_RUN", "").lower() in ("1", "true", "yes")


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


def _delay() -> None:
    if DEMO_DELAY_SECONDS > 0:
        time.sleep(DEMO_DELAY_SECONDS)


def _convo(agent: str, message: str, sublines: Optional[list[str]] = None) -> None:
    label = f"  [{agent}]"
    print(f"{label} {message}")
    if sublines:
        for line in sublines:
            print(f"       {line}")
    print()
    _delay()


def _seller_headers() -> dict[str, str]:
    if SELLER_API_KEY:
        return {"X-API-Key": SELLER_API_KEY}
    return {}


def _buyer_headers() -> dict[str, str]:
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


def search_media_kit_buyer_or_seller(query: str) -> list[dict[str, Any]]:
    try:
        resp = post_json(
            f"{BUYER_URL}/media-kit/search",
            {"query": query},
            headers=_buyer_headers(),
        )
        return resp.get("packages", [])
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (404, 401):
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


def fetch_seller_linear_products() -> list[dict[str, Any]]:
    data = get_json(f"{SELLER_URL}/products", headers=_seller_headers())
    products = data.get("products", [])
    if not isinstance(products, list):
        return []
    return [p for p in products if str(p.get("inventory_type", "")).lower() == "linear_tv"]


def match_cbs_lines(linear: list[dict[str, Any]]) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """Return (broadcast, news, cable) product dicts from seller catalog."""

    def lower(p: dict[str, Any]) -> str:
        return f"{p.get('name', '')} {p.get('description', '')}".lower()

    broadcast = None
    news = None
    cable = None
    for p in linear:
        n = lower(p)
        if "cbs" in n and "primetime" in n and "spanish" not in n:
            broadcast = p
        elif "cbs" in n and "news" in n:
            news = p
        elif "cbs" in n and "cable" in n:
            cable = p
        elif cable is None and ("bet" in n or "mtv" in n or "cmt" in n):
            cable = p
    # Fallbacks
    if broadcast is None:
        for p in linear:
            if "primetime" in lower(p):
                broadcast = p
                break
    if news is None:
        for p in linear:
            if "news" in lower(p):
                news = p
                break
    if cable is None:
        for p in linear:
            if p is not broadcast and p is not news:
                cable = p
                break
    return broadcast, news, cable


def discover_seller_paths() -> tuple[Optional[str], Optional[str]]:
    proposal_path: Optional[str] = None
    negotiation_path: Optional[str] = None
    try:
        data = get_json(f"{SELLER_URL}/openapi.json", headers=_seller_headers())
        paths = data.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict) or "post" not in methods:
                continue
            pl = path.lower()
            if "counter" in pl or "negotiation" in pl:
                negotiation_path = negotiation_path or path
            elif "proposal" in pl or "quote" in pl:
                proposal_path = proposal_path or path
    except Exception:
        pass
    return proposal_path, negotiation_path


def create_proposal(
    product_id: str,
    price: float,
    proposal_endpoint: Optional[str],
) -> dict[str, Any]:
    proposals_payload = {
        "product_id": product_id,
        "deal_type": "PD",
        "price": price,
        "impressions": 1_000_000,
        "start_date": "2026-04-01",
        "end_date": "2026-06-30",
        "buyer_id": "demo-buyer-cbs-linear",
        "advertiser_id": "demo-advertiser",
        "agency_id": "demo-agency",
        "agent_url": BUYER_URL,
    }
    quote_payload = {
        "product_id": product_id,
        "deal_type": "PD",
        "impressions": 1_000_000,
        "flight_start": "2026-04-01",
        "flight_end": "2026-06-30",
        "target_cpm": price,
        "buyer_identity": {"agency_id": "demo-agency", "advertiser_id": "demo-advertiser"},
        "agent_url": BUYER_URL,
    }
    headers = _seller_headers()
    if SELLER_PROPOSAL_ENDPOINT:
        try:
            return post_json(SELLER_PROPOSAL_ENDPOINT, proposals_payload, headers=headers)
        except Exception:
            return post_json(SELLER_PROPOSAL_ENDPOINT, quote_payload, headers=headers)
    if proposal_endpoint:
        try:
            return post_json(proposal_endpoint, proposals_payload, headers=headers)
        except Exception:
            return post_json(proposal_endpoint, quote_payload, headers=headers)
    for endpoint, payload in (
        (f"{SELLER_URL}/api/v1/quotes", quote_payload),
        (f"{SELLER_URL}/proposals", proposals_payload),
    ):
        try:
            return post_json(endpoint, payload, headers=headers)
        except Exception:
            continue
    raise RuntimeError("Could not create proposal on seller (tried /api/v1/quotes and /proposals)")


def negotiate_round(
    proposal_id: str,
    buyer_price: float,
    negotiation_path_template: Optional[str],
) -> dict[str, Any]:
    payload = {"buyer_price": float(buyer_price), "buyer_tier": "public"}
    urls: list[str] = []
    if SELLER_NEGOTIATION_PATH:
        p = SELLER_NEGOTIATION_PATH.replace("{proposal_id}", proposal_id)
        urls.append(f"{SELLER_URL.rstrip('/')}{p}" if not p.startswith("http") else p)
    elif negotiation_path_template:
        p = negotiation_path_template.replace("{proposal_id}", proposal_id).replace("{id}", proposal_id)
        urls.append(f"{SELLER_URL.rstrip('/')}{p}" if not p.startswith("http") else p)
    else:
        urls = [f"{SELLER_URL.rstrip('/')}/proposals/{proposal_id}/counter"]
    last_err: Optional[Exception] = None
    for url in urls:
        for pl in (payload, {**payload, "agency_id": "demo-agency", "advertiser_id": "demo-advertiser"}):
            try:
                return post_json(url, pl, headers=_seller_headers())
            except Exception as e:
                last_err = e
    raise RuntimeError(f"Negotiation failed: {last_err}")


def extract_proposal_id(response: dict[str, Any]) -> Optional[str]:
    for key in ("proposal_id", "quote_id", "id"):
        if response.get(key):
            return str(response[key])
    return None


def extract_price(response: dict[str, Any]) -> Optional[float]:
    for key in ("final_price", "accepted_price", "counter_offer", "counter_price", "seller_price", "price", "current_price"):
        v = response.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def extract_status(response: dict[str, Any]) -> str:
    return str(response.get("status", response.get("recommendation", response.get("action", "unknown")))).lower()


def extract_negotiation_action(response: dict[str, Any]) -> str:
    """Seller counter endpoint returns action: accept | counter | reject | final_offer."""
    return str(response.get("action", "")).lower()


def negotiation_terminal_success(response: dict[str, Any]) -> bool:
    if extract_negotiation_action(response) == "accept":
        return True
    st = str(response.get("status", "")).lower()
    return st in ("accepted", "approved")


def _product_summary(p: Optional[dict[str, Any]], label: str) -> list[str]:
    if not p:
        return [f"{label}: (not in catalog — describe verbally in demo)"]
    return [
        f"{label}: {p.get('name', 'Unknown')}",
        f"  Base CPM ${float(p.get('base_cpm', 0)):.2f} | Floor ${float(p.get('floor_cpm', 0)):.2f}",
    ]


def main() -> None:
    print("=" * 64)
    print("DEMO: Linear TV buy — CBS Broadcast + CBS News + Paramount cable")
    print("=" * 64)
    print(f"Buyer API:  {BUYER_URL}")
    print(f"Seller API: {SELLER_URL}")
    if DEMO_DELAY_SECONDS > 0:
        print(f"Pause between turns: {DEMO_DELAY_SECONDS}s (DEMO_DELAY_SECONDS=0 to disable)")
    if DEMO_DRY_RUN:
        print("DEMO_DRY_RUN=1 — conversation only at close (no live proposal/negotiation)")
    print()
    _delay()

    proposal_path, negotiation_path = discover_seller_paths()
    proposal_endpoint = f"{SELLER_URL.rstrip('/')}{proposal_path}" if proposal_path else None
    if proposal_path and not proposal_path.startswith("/"):
        proposal_endpoint = proposal_path

    _convo(
        "BUYER AGENT",
        "We need a Q2 linear TV plan for a national auto launch. "
        "Prioritize three pillars: (1) CBS broadcast primetime, "
        "(2) CBS News dayparts / run-of-news, (3) Paramount cable nets (BET, MTV, CMT). "
        "Goal: one coordinated buy with clear CPMs per line.",
    )

    _convo(
        "SELLER AGENT",
        "Understood. I'll pull our linear catalog and map your pillars to rate cards — "
        "broadcast, news, and cable are priced on different yield curves. "
        "One moment while I sync products.",
    )

    try:
        linear = fetch_seller_linear_products()
    except Exception as e:
        _convo("SELLER AGENT", f"I couldn't load /products: {e}. Check seller is running and SELLER_API_KEY if required.")
        sys.exit(1)

    b, n, c = match_cbs_lines(linear)
    _convo(
        "SELLER AGENT",
        f"I have {len(linear)} linear_tv product(s) on file. Here's how they line up with your ask:",
        sublines=_product_summary(b, "1) CBS Broadcast")
        + _product_summary(n, "2) CBS News")
        + _product_summary(c, "3) Paramount cable nets"),
    )

    _convo(
        "BUYER AGENT",
        "Confirming: we want simultaneous delivery — broadcast for reach, news for "
        "contextual news adjacency, cable for younger demos on Paramount brands. "
        "Can you surface any packaged bundles in the media kit for 'CBS' or 'linear'?",
    )

    try:
        mk_hits = search_media_kit_buyer_or_seller("CBS linear")
    except Exception:
        try:
            mk_hits = search_media_kit_buyer_or_seller("CBS")
        except Exception as e:
            mk_hits = []
            _convo("SELLER AGENT", f"Media-kit search unavailable ({e}); continuing with catalog-only.")
    if mk_hits:
        lines = [f"{len(mk_hits)} package(s) matched search:"]
        for i, pkg in enumerate(mk_hits[:5], 1):
            lines.append(f"  {i}. {pkg.get('name', 'Unknown')} — {pkg.get('price_range', pkg.get('description', ''))[:60]}")
        _convo("SELLER AGENT", "From the media kit:", sublines=lines)
    else:
        _convo(
            "SELLER AGENT",
            "No extra media-kit hits for that query; we're good to proceed on rated "
            "catalog lines (broadcast / news / cable) above.",
        )

    floors = [float(x.get("floor_cpm", 0)) for x in (b, n, c) if x]
    bases = [float(x.get("base_cpm", 0)) for x in (b, n, c) if x]
    if not floors:
        _convo("BUYER AGENT", "We don't have three resolved lines in the API response — stopping before proposal.")
        sys.exit(1)

    avg_floor = sum(floors) / len(floors)
    avg_base = sum(bases) / len(bases) if bases else avg_floor * 1.2
    primary = b or n or c
    assert primary is not None
    pid = str(primary.get("product_id", ""))

    # Opening must be >= this product's floor (else seller rejects) and < base (so we get a counter, not instant accept)
    floor_p = float(primary.get("floor_cpm", 0))
    base_p = float(primary.get("base_cpm", 0))
    if base_p <= floor_p:
        base_p = floor_p + 15.0
    opening = round(floor_p + (base_p - floor_p) * 0.22, 2)
    opening = max(opening, floor_p)
    opening = min(opening, base_p - 0.5)

    _convo(
        "BUYER AGENT",
        f"Let's anchor the deal on our broadcast line ({primary.get('name')}) for system workflow, "
        f"with the other two lines in the IO attachment. "
        f"Opening CPM ${opening:.2f} on the anchor (floor ${floor_p:.2f}, list ${base_p:.2f}; "
        f"blended context across all three lines: avg floor ~${avg_floor:.2f}, avg list ~${avg_base:.2f}).",
    )

    _convo(
        "SELLER AGENT",
        "I'll register the proposal under that product_id and run it through pricing. "
        "If the engine counters, we can negotiate in-thread.",
    )

    if DEMO_DRY_RUN:
        _convo("BUYER AGENT", "(DRY RUN) Skipping POST /proposals and negotiation.")
        print("Done.")
        return

    try:
        prop_resp = create_proposal(pid, opening, proposal_endpoint)
    except Exception as e:
        _convo("SELLER AGENT", f"Proposal error: {e}")
        sys.exit(1)

    prop_id = extract_proposal_id(prop_resp)
    st = extract_status(prop_resp)
    if st in ("accepted", "approved"):
        px = extract_price(prop_resp)
        _convo("SELLER AGENT", f"Accepted immediately at ${px:.2f} CPM." if px else "Accepted.")
        print("Done.")
        return

    if not prop_id:
        _convo("SELLER AGENT", f"No proposal_id returned: {pretty(prop_resp)}")
        sys.exit(1)

    _convo("SELLER AGENT", f"Proposal recorded (id {prop_id}). Evaluating your ${opening:.2f} CPM anchor offer…")

    # --- Round 1: opening bid (expect counter or final_offer from seller) ---
    _convo(
        "BUYER AGENT",
        f"Round 1: I'm at ${opening:.2f} CPM on the broadcast anchor — strong volume across all three pillars.",
    )

    try:
        neg1 = negotiate_round(prop_id, opening, negotiation_path)
    except Exception as e:
        _convo("SELLER AGENT", f"Negotiation round 1 failed: {e}")
        sys.exit(1)

    act1 = extract_negotiation_action(neg1)
    sp1 = neg1.get("seller_price")
    sp1f = float(sp1) if sp1 is not None else None

    if act1 == "reject":
        _convo("SELLER AGENT", neg1.get("rationale", "Offer below floor — cannot proceed.") or "Rejected.")
        sys.exit(1)

    if negotiation_terminal_success(neg1):
        final = sp1f or opening
        print("  " + "=" * 60)
        _convo("SELLER AGENT", f"Accepted in round 1 at ${final:.2f} CPM.")
        print("Demo complete.")
        return

    if sp1f is not None:
        _convo(
            "SELLER AGENT",
            f"Round 1 — {act1.replace('_', ' ')}: I'm at ${sp1f:.2f} CPM on the anchor. "
            f"({neg1.get('rationale', '')[:120]}…)"
            if len(str(neg1.get("rationale", ""))) > 120
            else f"Round 1 — {act1.replace('_', ' ')}: I'm at ${sp1f:.2f} CPM. {neg1.get('rationale', '')}",
        )
    else:
        _convo("SELLER AGENT", f"Round 1 — {act1}: see rationale in system.")

    # --- Round 2: meet seller's ask (engine accepts when buyer_price >= last seller price) ---
    meet = round(sp1f if sp1f is not None else base_p, 2)
    _convo(
        "BUYER AGENT",
        f"Round 2: I'll move to ${meet:.2f} CPM to lock the bundle — News and Paramount cable lines stay on the IO as agreed.",
    )

    try:
        neg2 = negotiate_round(prop_id, meet, negotiation_path)
    except Exception as e:
        _convo("SELLER AGENT", f"Negotiation round 2 failed: {e}")
        sys.exit(1)

    if negotiation_terminal_success(neg2):
        final = float(neg2.get("seller_price", meet))
        print("  " + "=" * 60)
        _convo(
            "SELLER AGENT",
            f"Round 2 — accepted at ${final:.2f} CPM on the anchor. "
            f"I'll draft the IO with CBS News + Paramount cable at the attached rates.",
        )
    else:
        act2 = extract_negotiation_action(neg2)
        _convo(
            "SELLER AGENT",
            f"Round 2 did not close (action={act2}). "
            f"Try raising toward list ${base_p:.2f} or continue in seller UI.",
        )

    print("Demo complete.")


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
