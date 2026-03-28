#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simulation: IAB Deals API quote-then-book + optional buyer booking job.

Flow A (seller — always runs if seller is up):
  1. GET  {seller}/products — pick CTV **Paramount Streaming RON**
     (`prod-ctv-paramount-streaming-ron`), overridable via QUOTE_PRODUCT_ID / QUOTE_PRODUCT_NAME.
  2. POST {seller}/api/v1/quotes — non-binding quote (PD or PG)
  3. GET  {seller}/api/v1/quotes/{quote_id} — retrieve quote
  4. POST {seller}/api/v1/deals — book from quote
  5. GET  {seller}/api/v1/deals/{deal_id} — confirm deal record

Flow B (buyer API — optional):
  POST {buyer}/bookings with a campaign brief and poll status.
  Requires buyer OpenDirect/MCP configuration; off by default.

Usage:
  SELLER_URL=http://localhost:8001 python3 scripts/simulate_quote_and_deal_booking.py
  SELLER_API_KEY=... BUYER_URL=http://localhost:8000 BUYER_API_KEY=... \\
    RUN_BUYER_BOOKING_JOB=1 python3 scripts/simulate_quote_and_deal_booking.py

  DEMO_DELAY_SECONDS=0   # default is 3s between agent lines; set 0 for no pause
  RUN_BUYER_BOOKING_JOB=1  # also POST /bookings on buyer (needs OpenDirect config)
  DEAL_TYPE=PD             # PD | PG (PG requires impressions)
  QUOTE_PRODUCT_ID=prod-ctv-paramount-streaming-ron   # default CTV Paramount RON
  QUOTE_PRODUCT_NAME=    # optional: match product name substring if id missing

Note: Seller must assign stable product_id values across requests (same catalog for
GET /products and POST /api/v1/quotes). If you see product_not_found after a fresh
GET /products, restart seller-agent with an up-to-date ProductSetupFlow.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Optional

import requests

BUYER_URL = os.getenv("BUYER_URL", "http://localhost:8000").rstrip("/")
SELLER_URL = os.getenv("SELLER_URL", "http://localhost:8001").rstrip("/")
SELLER_API_KEY = os.getenv("SELLER_API_KEY", "").strip()
BUYER_API_KEY = os.getenv("BUYER_API_KEY", "").strip()
# Pause after each [BUYER AGENT] / [SELLER AGENT] / [BUYER API] line (default 3s for demos).
DEMO_DELAY_SECONDS = float(os.getenv("DEMO_DELAY_SECONDS", "3"))
RUN_BUYER_BOOKING_JOB = os.getenv("RUN_BUYER_BOOKING_JOB", "").lower() in ("1", "true", "yes")
DEAL_TYPE = os.getenv("DEAL_TYPE", "PD").strip().upper()
# Default: CTV Paramount Streaming RON (seller ProductSetupFlow + mock CTV bundle).
DEFAULT_QUOTE_PRODUCT_ID = "prod-ctv-paramount-streaming-ron"
QUOTE_PRODUCT_ID = os.getenv("QUOTE_PRODUCT_ID", DEFAULT_QUOTE_PRODUCT_ID).strip()
QUOTE_PRODUCT_NAME = os.getenv("QUOTE_PRODUCT_NAME", "").strip()
POLL_INTERVAL_SEC = float(os.getenv("BOOKING_POLL_INTERVAL_SEC", "2"))
BOOKING_POLL_MAX = int(os.getenv("BOOKING_POLL_MAX", "60"))


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


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


def _seller_headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if SELLER_API_KEY:
        h["X-API-Key"] = SELLER_API_KEY
    return h


def _buyer_headers() -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if BUYER_API_KEY:
        h["X-API-Key"] = BUYER_API_KEY
    return h


def get_json(url: str, **kwargs: Any) -> Any:
    r = requests.get(url, timeout=60, **kwargs)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict[str, Any], **kwargs: Any) -> Any:
    r = requests.post(url, json=payload, timeout=120, **kwargs)
    r.raise_for_status()
    return r.json()


def fetch_quote_product() -> tuple[str, dict[str, Any]]:
    """Pick product for quoting: QUOTE_PRODUCT_ID, else QUOTE_PRODUCT_NAME substring, else first listing."""
    data = get_json(f"{SELLER_URL}/products", headers=_seller_headers())
    products = data.get("products", [])
    if not products:
        raise RuntimeError("Seller returned no products. Run seller ProductSetupFlow (GET /products once).")

    if QUOTE_PRODUCT_ID:
        for p in products:
            pid = str(p.get("product_id") or p.get("id") or "")
            if pid == QUOTE_PRODUCT_ID:
                return pid, p

    if QUOTE_PRODUCT_NAME:
        needle = QUOTE_PRODUCT_NAME.lower()
        for p in products:
            name = (p.get("name") or "").lower()
            if needle in name:
                pid = str(p.get("product_id") or p.get("id") or "")
                if pid:
                    return pid, p

    p0 = products[0]
    pid = str(p0.get("product_id") or p0.get("id") or "")
    if not pid:
        raise RuntimeError("Could not read product_id from seller catalog.")
    return pid, p0


def request_quote(product_id: str, impressions: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "product_id": product_id,
        "deal_type": DEAL_TYPE,
        "impressions": impressions,
        "flight_start": "2026-04-01",
        "flight_end": "2026-06-30",
        "buyer_identity": {
            "agency_id": "demo-agency",
            "advertiser_id": "demo-advertiser",
        },
    }
    if DEAL_TYPE == "PG" and not impressions:
        body["impressions"] = 1_000_000
    return post_json(f"{SELLER_URL}/api/v1/quotes", body, headers=_seller_headers())


def book_from_quote(quote_id: str) -> dict[str, Any]:
    body = {
        "quote_id": quote_id,
        "buyer_identity": {"agency_id": "demo-agency", "advertiser_id": "demo-advertiser"},
        "notes": "simulate_quote_and_deal_booking.py — demo booking",
    }
    return post_json(f"{SELLER_URL}/api/v1/deals", body, headers=_seller_headers())


def run_buyer_booking_job() -> None:
    _say(
        "BUYER API",
        "Starting CrewAI deal booking job (POST /bookings, auto_approve) …",
        sublines=[
            "Uses buyer OpenDirect settings; may fail if OPENDIRECT_BASE_URL / MCP is unset.",
        ],
    )
    brief = {
        "name": "Quote-to-Book parallel demo",
        "objectives": ["awareness", "reach"],
        "budget": 150000.0,
        "start_date": "2026-04-01",
        "end_date": "2026-06-30",
        "target_audience": {"demo": "A25-54", "geo": "US"},
        "kpis": {"cpm_guidance": "competitive"},
        "channels": ["ctv", "video"],
    }
    try:
        resp = post_json(
            f"{BUYER_URL}/bookings",
            {"brief": brief, "auto_approve": True},
            headers=_buyer_headers(),
        )
    except requests.HTTPError as e:
        _say("BUYER API", f"POST /bookings failed: {e}")
        if e.response is not None:
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:800])
        return

    job_id = resp.get("job_id")
    if not job_id:
        _say("BUYER API", f"Unexpected response: {pretty(resp)}")
        return

    _say("BUYER API", f"Job started: {job_id}. Polling GET /bookings/{{id}} …")

    for _ in range(BOOKING_POLL_MAX):
        time.sleep(POLL_INTERVAL_SEC)
        try:
            st = get_json(f"{BUYER_URL}/bookings/{job_id}", headers=_buyer_headers())
        except Exception as e:
            _say("BUYER API", f"Poll error: {e}")
            return

        status = st.get("status", "")
        prog = st.get("progress", 0)
        print(f"       ... status={status} progress={prog}")
        if status in ("completed", "failed"):
            _say(
                "BUYER API",
                f"Terminal status: {status}",
                sublines=[pretty(st)[:4000]],
            )
            return

    _say("BUYER API", f"Timed out after {BOOKING_POLL_MAX * POLL_INTERVAL_SEC:.0f}s polling.")


def main() -> None:
    print("=" * 64)
    print("SIMULATION: Quote (IAB Deals API) -> Deal booking")
    print("=" * 64)
    print(f"Seller: {SELLER_URL}" + (" (authenticated)" if SELLER_API_KEY else ""))
    print(f"Buyer:  {BUYER_URL}" + (" (authenticated)" if BUYER_API_KEY else ""))
    print(f"Deal type: {DEAL_TYPE}")
    print(f"Quote product: {QUOTE_PRODUCT_ID or '(by name)'}" + (f" / name ~ {QUOTE_PRODUCT_NAME!r}" if QUOTE_PRODUCT_NAME else ""))
    if DEMO_DELAY_SECONDS > 0:
        print(f"Demo delay: {DEMO_DELAY_SECONDS}s between steps")
    print()

    # --- Flow A: quote then book on seller ---
    _say(
        "BUYER AGENT",
        "Resolving CTV product (Paramount Streaming RON) from the seller catalog for quoting …",
    )
    product_id, product_row = fetch_quote_product()
    if QUOTE_PRODUCT_ID and product_id != QUOTE_PRODUCT_ID:
        print(
            f"  Warning: QUOTE_PRODUCT_ID={QUOTE_PRODUCT_ID!r} not in catalog; using {product_id!r} instead.",
            file=sys.stderr,
        )
    min_imp = int(product_row.get("minimum_impressions") or 10_000)
    impressions = max(1_000_000, min_imp)

    _say(
        "SELLER AGENT",
        f"Using product_id={product_id} ({product_row.get('name', '')}). "
        f"Requesting quote: {DEAL_TYPE}, {impressions:,} impressions.",
    )

    try:
        quote = request_quote(product_id, impressions)
    except requests.HTTPError as e:
        print(f"Quote failed: {e}")
        if e.response is not None:
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:1000])
        sys.exit(1)

    quote_id = quote.get("quote_id")
    if not quote_id:
        print("No quote_id in response:", pretty(quote))
        sys.exit(1)

    pricing = quote.get("pricing") or {}
    terms = quote.get("terms") or {}
    _say(
        "SELLER AGENT",
        f"Quote issued: {quote_id}",
        sublines=[
            f"Status: {quote.get('status')}",
            f"Final CPM: ${pricing.get('final_cpm')} (base ${pricing.get('base_cpm')})",
            f"Flight: {terms.get('flight_start')} -> {terms.get('flight_end')}",
            f"Impressions: {terms.get('impressions')}",
        ],
    )

    _say("BUYER AGENT", f"Fetching quote record GET /api/v1/quotes/{quote_id} …")
    try:
        quote_again = get_json(f"{SELLER_URL}/api/v1/quotes/{quote_id}", headers=_seller_headers())
    except requests.HTTPError as e:
        print(f"GET quote failed: {e}")
        sys.exit(1)

    _say(
        "BUYER AGENT",
        "Quote confirmed in storage.",
        sublines=[f"status={quote_again.get('status')}, expires_at={quote_again.get('expires_at')}"],
    )

    _say("BUYER AGENT", "Committing: POST /api/v1/deals (book from quote) …")
    try:
        deal = book_from_quote(quote_id)
    except requests.HTTPError as e:
        print(f"Book deal failed: {e}")
        if e.response is not None:
            try:
                print(pretty(e.response.json()))
            except Exception:
                print(e.response.text[:1000])
        sys.exit(1)

    deal_id = deal.get("deal_id")
    if not deal_id:
        print("No deal_id in response:", pretty(deal))
        sys.exit(1)

    dpricing = deal.get("pricing") or {}
    _say(
        "SELLER AGENT",
        f"Deal created: {deal_id}",
        sublines=[
            f"status: {deal.get('status')}",
            f"final CPM: ${dpricing.get('final_cpm')}",
            f"activation (sample): {list((deal.get('activation_instructions') or {}).keys())[:3]}",
        ],
    )

    _say("BUYER AGENT", f"GET /api/v1/deals/{deal_id} — verify persisted deal …")
    try:
        deal_get = get_json(f"{SELLER_URL}/api/v1/deals/{deal_id}", headers=_seller_headers())
    except requests.HTTPError as e:
        print(f"GET deal failed: {e}")
        sys.exit(1)

    _say(
        "BUYER AGENT",
        "Deal record retrieved.",
        sublines=[f"status={deal_get.get('status')}, quote_id={deal_get.get('quote_id')}"],
    )

    print("  " + "=" * 60)
    print("  Quote -> Deal path complete.")
    print(f"  quote_id={quote_id}  deal_id={deal_id}")
    print()

    if RUN_BUYER_BOOKING_JOB:
        run_buyer_booking_job()
    else:
        print("  (Set RUN_BUYER_BOOKING_JOB=1 to also run buyer POST /bookings workflow.)")

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
                print(e.response.text[:800])
        sys.exit(1)
