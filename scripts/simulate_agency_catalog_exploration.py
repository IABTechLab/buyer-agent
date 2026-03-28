#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulate an agency exploring the seller's catalog via HTTP (discovery / planning).

Steps:
  1. Health check
  2. Public scout — GET /products (no API key) → CPM bands + metadata when seller uses
     tiered catalog responses
  3. Optional — GET /media-kit/packages (public package browser)
  4. Authenticated planner — GET /products and GET /products/{id} with X-Api-Key → exact CPMs
  5. POST /pricing — agency-tier quote guidance for a chosen product
  6. POST /discovery — natural-language inventory question (no agent_url → no registry gate)

Usage:
  SELLER_URL=http://localhost:8001 python3 scripts/simulate_agency_catalog_exploration.py

  SELLER_API_KEY=sk-... python3 scripts/simulate_agency_catalog_exploration.py
  DEMO_DELAY_SECONDS=2
  AGENCY_ID=demo-agency-001
  ADVERTISER_ID=demo-advertiser-001
  PRICING_VOLUME=5000000
  DISCOVERY_QUERY="What CTV sports packages do you have under $40 CPM?"
  SKIP_MEDIA_KIT=1          # skip GET /media-kit/packages if not seeded
  PRODUCT_DETAIL_ID=pplus_only   # optional; default = first product from list
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Optional

import requests

SELLER_URL = os.getenv("SELLER_URL", "http://localhost:8001").rstrip("/")
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "").strip()
DEMO_DELAY_SECONDS = float(os.getenv("DEMO_DELAY_SECONDS", "2"))
AGENCY_ID = os.getenv("AGENCY_ID", "demo-agency-001").strip()
ADVERTISER_ID = os.getenv("ADVERTISER_ID", "demo-advertiser-001").strip()
PRICING_VOLUME = int(os.getenv("PRICING_VOLUME", "5000000"))
DISCOVERY_QUERY = os.getenv(
    "DISCOVERY_QUERY",
    "Summarize premium CTV and streaming sports products suitable for a national awareness campaign.",
).strip()
SKIP_MEDIA_KIT = os.getenv("SKIP_MEDIA_KIT", "").lower() in ("1", "true", "yes")
PRODUCT_DETAIL_ID = os.getenv("PRODUCT_DETAIL_ID", "").strip()


def pretty(data: Any, limit: Optional[int] = None) -> str:
    s = json.dumps(data, indent=2, default=str)
    if limit and len(s) > limit:
        return s[:limit] + "\n  ... [truncated]"
    return s


def _delay() -> None:
    if DEMO_DELAY_SECONDS > 0:
        time.sleep(DEMO_DELAY_SECONDS)


def _say(who: str, msg: str, sublines: Optional[list[str]] = None) -> None:
    print(f"  [{who}] {msg}")
    if sublines:
        for line in sublines:
            print(f"       {line}")
    print()
    _delay()


def headers(*, with_key: bool) -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json", "Content-Type": "application/json"}
    if with_key and SELLER_API_KEY:
        h["X-API-Key"] = SELLER_API_KEY
    return h


def get_json(url: str, **kwargs: Any) -> Any:
    r = requests.get(url, timeout=60, **kwargs)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
    r = requests.post(url, json=payload, timeout=120, **kwargs)
    r.raise_for_status()
    return r.json()


def _summarize_products(products: list[dict[str, Any]], max_rows: int = 12) -> list[str]:
    lines: list[str] = []
    for p in products[:max_rows]:
        pid = p.get("product_id") or p.get("agent_id") or "?"
        name = (p.get("name") or "")[:48]
        vis = p.get("pricing_visibility", "")
        if p.get("base_cpm") is not None:
            price = f"${p.get('floor_cpm')}-${p.get('base_cpm')} CPM"
        elif p.get("cpm_range_usd"):
            rg = p["cpm_range_usd"]
            price = f"${rg.get('low')}-${rg.get('high')} CPM (range)"
        else:
            price = "CPM n/a"
        tier = p.get("package_tier") or ""
        ch = p.get("channel") or ""
        extra = ", ".join(x for x in (tier, ch) if x)
        suffix = f" ({extra})" if extra else ""
        lines.append(f"{pid}: {name} — {price}{suffix} [{vis or 'n/a'}]")
    if len(products) > max_rows:
        lines.append(f"... +{len(products) - max_rows} more products")
    return lines


def main() -> None:
    print("=" * 64)
    print("SIMULATION: Agency explores seller catalog")
    print("=" * 64)
    print(f"Seller: {SELLER_URL}")
    print(f"Agency: {AGENCY_ID}" + (f" | API key: set ({len(SELLER_API_KEY)} chars)" if SELLER_API_KEY else " | API key: not set (public-only pass)"))
    if DEMO_DELAY_SECONDS > 0:
        print(f"Demo delay: {DEMO_DELAY_SECONDS}s between steps")
    print()

    # --- 1. Health ---
    _say("AGENCY", "Checking seller availability (GET /health) …")
    try:
        health = get_json(f"{SELLER_URL}/health", headers=headers(with_key=False))
        _say("SELLER API", f"Health: {pretty(health)}")
    except requests.RequestException as e:
        print(f"ERROR: cannot reach seller: {e}")
        sys.exit(1)

    # --- 2. Public product catalog ---
    _say(
        "AGENCY (public scout)",
        "Browsing GET /products without credentials — expect descriptions + CPM ranges if tiering is enabled.",
    )
    try:
        pub = get_json(f"{SELLER_URL}/products", headers=headers(with_key=False))
        plist = pub.get("products") or []
        _say(
            "SELLER API",
            f"Returned {len(plist)} products (public view).",
            sublines=_summarize_products(plist),
        )
    except requests.HTTPError as e:
        _say("SELLER API", f"GET /products failed: {e}")
        plist = []

    if not plist:
        print("No products returned; enable seller catalog or DISCOVERY_CATALOG_MODE + seed.")
        sys.exit(1)

    detail_id = PRODUCT_DETAIL_ID or str(plist[0].get("product_id") or plist[0].get("agent_id") or "")

    _say(
        "AGENCY (public scout)",
        f"Drilling into GET /products/{detail_id} (still unauthenticated) …",
    )
    try:
        one_pub = get_json(
            f"{SELLER_URL}/products/{detail_id}",
            headers=headers(with_key=False),
        )
        _say(
            "SELLER API",
            "Product detail (public).",
            sublines=[pretty(one_pub, limit=2500)],
        )
    except requests.HTTPError as e:
        _say("SELLER API", f"GET product detail failed: {e}")

    # --- 3. Media kit (optional) ---
    if not SKIP_MEDIA_KIT:
        _say("AGENCY (public scout)", "Opening GET /media-kit/packages …")
        try:
            mk = get_json(f"{SELLER_URL}/media-kit/packages", headers=headers(with_key=False))
            pkgs = mk.get("packages") or []
            _say(
                "SELLER API",
                f"Media kit: {len(pkgs)} package(s).",
                sublines=[pretty(pkgs[:3], limit=2000)] if pkgs else ["(no packages)"],
            )
        except requests.RequestException as e:
            _say("SELLER API", f"Media kit skipped: {e}")

    # --- 4. Authenticated catalog ---
    if not SELLER_API_KEY:
        _say(
            "AGENCY",
            "No SELLER_API_KEY — skipping authenticated product views, /pricing, and keyed media search.",
            sublines=["Export SELLER_API_KEY=... to simulate seat/agency authenticated exploration."],
        )
    else:
        _say(
            "AGENCY (authenticated)",
            "Same catalog with X-Api-Key — seller should return exact base/floor CPM when supported.",
        )
        auth = get_json(f"{SELLER_URL}/products", headers=headers(with_key=True))
        auth_list = auth.get("products") or []
        _say(
            "SELLER API",
            f"Returned {len(auth_list)} products (authenticated view).",
            sublines=_summarize_products(auth_list),
        )

        try:
            one_auth = get_json(
                f"{SELLER_URL}/products/{detail_id}",
                headers=headers(with_key=True),
            )
            _say(
                "SELLER API",
                f"Product detail for {detail_id} (authenticated).",
                sublines=[pretty(one_auth, limit=2500)],
            )
        except requests.HTTPError as e:
            _say("SELLER API", f"GET product detail (auth) failed: {e}")

        # --- 5. Pricing ---
        _say(
            "AGENCY (planner)",
            f"POST /pricing for product_id={detail_id}, buyer_tier implied via API key + agency_id …",
        )
        try:
            pr = post_json(
                f"{SELLER_URL}/pricing",
                {
                    "product_id": detail_id,
                    "buyer_tier": "agency",
                    "agency_id": AGENCY_ID,
                    "advertiser_id": ADVERTISER_ID,
                    "volume": PRICING_VOLUME,
                },
                headers=headers(with_key=True),
            )
            _say(
                "SELLER API",
                "Pricing decision.",
                sublines=[
                    f"final_price={pr.get('final_price')} {pr.get('currency', 'USD')} "
                    f"(base={pr.get('base_price')}, tier_disc={pr.get('tier_discount')}, "
                    f"vol_disc={pr.get('volume_discount')})",
                    (pr.get("rationale") or "")[:400],
                ],
            )
        except requests.HTTPError as e:
            _say("SELLER API", f"POST /pricing failed: {e}")

        # --- 6. Media kit search (authenticated) ---
        if not SKIP_MEDIA_KIT:
            _say("AGENCY", "POST /media-kit/search with API key for richer package matches …")
            try:
                search = post_json(
                    f"{SELLER_URL}/media-kit/search",
                    {
                        "query": "CTV premium sports",
                        "buyer_tier": "agency",
                        "agency_id": AGENCY_ID,
                        "advertiser_id": ADVERTISER_ID,
                    },
                    headers=headers(with_key=True),
                )
                res = search.get("results") or []
                _say(
                    "SELLER API",
                    f"Search returned {len(res)} result(s).",
                    sublines=[pretty(res[:5], limit=2000)],
                )
            except requests.RequestException as e:
                _say("SELLER API", f"Media kit search skipped: {e}")

    # --- 7. Natural-language discovery (works without API key; omit agent_url) ---
    _say(
        "AGENCY",
        "POST /discovery — natural language question (buyer_tier=agency, no agent_url to avoid registry gate).",
    )
    disc_headers = headers(with_key=bool(SELLER_API_KEY))
    try:
        disc = post_json(
            f"{SELLER_URL}/discovery",
            {
                "query": DISCOVERY_QUERY,
                "buyer_tier": "agency",
                "agency_id": AGENCY_ID,
            },
            headers=disc_headers,
        )
        _say(
            "SELLER API",
            "Discovery response.",
            sublines=[pretty(disc, limit=4000)],
        )
    except requests.HTTPError as e:
        _say("SELLER API", f"POST /discovery failed: {e}")
        if e.response is not None:
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:1200])

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print("\nHTTP error:", e)
        if e.response is not None:
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:1200])
        sys.exit(1)
