# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seed data for the DealJockey demo dashboard.

Populates the DealStore with 14 realistic sample deals spanning
multiple media types, statuses, deal types, sellers, and price ranges.
Also seeds portfolio_metadata, deal_activations, and performance_cache
for selected deals to demonstrate the full v2 schema.
"""

import json
import logging
from datetime import UTC, datetime

from ..storage.deal_store import DealStore

logger = logging.getLogger(__name__)

# Convenience timestamp
_NOW = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def seed_demo_data(store: DealStore) -> list[str]:
    """Insert 14 example deals with related metadata into the store.

    Returns:
        List of created deal IDs.
    """
    deals = _build_deals()
    created_ids: list[str] = []

    for spec in deals:
        # Extract ancillary data before saving the deal
        meta = spec.pop("_portfolio_metadata", None)
        activations = spec.pop("_activations", None)
        perf = spec.pop("_performance_cache", None)

        deal_id = store.save_deal(**spec)
        created_ids.append(deal_id)

        # Portfolio metadata
        if meta:
            tags_val = meta.get("tags")
            if isinstance(tags_val, list):
                tags_val = json.dumps(tags_val)
            store.save_portfolio_metadata(
                deal_id=deal_id,
                import_source=meta.get("import_source", "SEED"),
                import_date=_NOW[:10],
                tags=tags_val,
                advertiser_id=meta.get("advertiser_id"),
                agency_id=meta.get("agency_id"),
            )

        # Deal activations
        if activations:
            for act in activations:
                store.save_deal_activation(
                    deal_id=deal_id,
                    platform=act["platform"],
                    platform_deal_id=act.get("platform_deal_id"),
                    activation_status=act.get("activation_status", "ACTIVE"),
                    last_sync_at=act.get("last_sync_at", _NOW),
                )

        # Performance cache
        if perf:
            store.save_performance_cache(
                deal_id=deal_id,
                impressions_delivered=perf.get("impressions_delivered"),
                spend_to_date=perf.get("spend_to_date"),
                fill_rate=perf.get("fill_rate"),
                win_rate=perf.get("win_rate"),
                avg_effective_cpm=perf.get("avg_effective_cpm"),
                last_delivery_at=perf.get("last_delivery_at", _NOW),
                performance_trend=perf.get("performance_trend", "STABLE"),
                cached_at=_NOW,
            )

        # Emit a seed event
        store.save_event(
            event_type="deal.imported",
            deal_id=deal_id,
            payload=json.dumps(
                {
                    "import_source": "SEED",
                    "display_name": spec.get("display_name", spec.get("product_name", "")),
                }
            ),
        )

    logger.info("Seeded %d demo deals", len(created_ids))
    return created_ids


def _build_deals() -> list[dict]:
    """Return a list of deal kwargs dicts (with ancillary _* keys)."""
    return [
        # 1. DIGITAL / PG / active -- ESPN
        {
            "seller_url": "https://espn.seller.example.com",
            "product_id": "espn-sports-pmp",
            "product_name": "ESPN Sports PMP",
            "display_name": "ESPN Sports PMP",
            "deal_type": "PG",
            "status": "active",
            "seller_deal_id": "ESPN-2026-Q1-001",
            "seller_org": "ESPN",
            "seller_domain": "espn.com",
            "seller_type": "PUBLISHER",
            "media_type": "DIGITAL",
            "price": 18.50,
            "fixed_price_cpm": 18.50,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 5000000,
            "flight_start": "2026-01-01",
            "flight_end": "2026-03-31",
            "description": "Premium sports display inventory across ESPN.com and app.",
            "geo_targets": "US",
            "formats": "display,native",
            "_portfolio_metadata": {
                "tags": ["premium", "sports", "Q1-2026"],
                "advertiser_id": "ADV-NIKE-001",
                "agency_id": "AGY-MEDIACOM-001",
                "import_source": "CSV",
            },
            "_activations": [
                {
                    "platform": "TTD",
                    "platform_deal_id": "TTD-ESPN-7890",
                    "activation_status": "ACTIVE",
                },
                {
                    "platform": "DV360",
                    "platform_deal_id": "DV-ESPN-4521",
                    "activation_status": "ACTIVE",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 3250000,
                "spend_to_date": 60125.00,
                "fill_rate": 0.72,
                "win_rate": 0.85,
                "avg_effective_cpm": 18.50,
                "performance_trend": "STABLE",
            },
        },
        # 2. DIGITAL / PD / active -- NYT
        {
            "seller_url": "https://nyt.seller.example.com",
            "product_id": "nyt-premium-news",
            "product_name": "NYT Premium News",
            "display_name": "NYT Premium News",
            "deal_type": "PD",
            "status": "active",
            "seller_deal_id": "NYT-2026-Q1-042",
            "seller_org": "The New York Times",
            "seller_domain": "nytimes.com",
            "seller_type": "PUBLISHER",
            "media_type": "DIGITAL",
            "price": 22.00,
            "fixed_price_cpm": 22.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 2000000,
            "flight_start": "2026-02-01",
            "flight_end": "2026-04-30",
            "description": "High-viewability news content targeting.",
            "content_categories": "IAB12,IAB12-1",
            "_portfolio_metadata": {
                "tags": ["news", "premium", "brand-safe"],
                "advertiser_id": "ADV-AMEX-002",
                "import_source": "CSV",
            },
            "_activations": [
                {
                    "platform": "TTD",
                    "platform_deal_id": "TTD-NYT-1234",
                    "activation_status": "ACTIVE",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 800000,
                "spend_to_date": 17600.00,
                "fill_rate": 0.65,
                "win_rate": 0.78,
                "avg_effective_cpm": 22.00,
                "performance_trend": "IMPROVING",
            },
        },
        # 3. DIGITAL / PA / active -- Condé Nast
        {
            "seller_url": "https://condenast.seller.example.com",
            "product_id": "cn-lifestyle",
            "product_name": "Condé Nast Lifestyle",
            "display_name": "Condé Nast Lifestyle PMP",
            "deal_type": "PA",
            "status": "active",
            "seller_deal_id": "CN-2026-LF-005",
            "seller_org": "Condé Nast",
            "seller_domain": "condenast.com",
            "seller_type": "PUBLISHER",
            "media_type": "DIGITAL",
            "price": 15.00,
            "bid_floor_cpm": 12.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 3000000,
            "flight_start": "2026-01-15",
            "flight_end": "2026-06-30",
            "description": "Lifestyle and fashion inventory across Vogue, GQ, Vanity Fair.",
            "content_categories": "IAB18,IAB9",
            "_portfolio_metadata": {
                "tags": ["lifestyle", "fashion", "premium"],
                "advertiser_id": "ADV-LVMH-003",
                "import_source": "MANUAL",
            },
        },
        # 4. DIGITAL / PG / active -- BuzzFeed
        {
            "seller_url": "https://buzzfeed.seller.example.com",
            "product_id": "bf-entertainment",
            "product_name": "BuzzFeed Entertainment",
            "display_name": "BuzzFeed Entertainment Package",
            "deal_type": "PG",
            "status": "active",
            "seller_deal_id": "BF-2026-ENT-010",
            "seller_org": "BuzzFeed",
            "seller_domain": "buzzfeed.com",
            "seller_type": "PUBLISHER",
            "media_type": "DIGITAL",
            "price": 8.50,
            "fixed_price_cpm": 8.50,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 10000000,
            "flight_start": "2026-03-01",
            "flight_end": "2026-05-31",
            "description": "High-volume entertainment and quiz content.",
        },
        # 5. CTV / PG / active -- Hulu
        {
            "seller_url": "https://hulu.seller.example.com",
            "product_id": "hulu-premium-ctv",
            "product_name": "Hulu Premium CTV",
            "display_name": "Hulu Premium CTV Package",
            "deal_type": "PG",
            "status": "active",
            "seller_deal_id": "HULU-2026-CTV-003",
            "seller_org": "Hulu",
            "seller_domain": "hulu.com",
            "seller_type": "PUBLISHER",
            "media_type": "CTV",
            "price": 35.00,
            "fixed_price_cpm": 35.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 1500000,
            "flight_start": "2026-01-01",
            "flight_end": "2026-06-30",
            "description": "Premium CTV spots across Hulu originals and live TV.",
            "formats": "video_15s,video_30s",
            "_portfolio_metadata": {
                "tags": ["ctv", "premium", "streaming"],
                "advertiser_id": "ADV-TOYOTA-004",
                "import_source": "CSV",
            },
            "_activations": [
                {
                    "platform": "TTD",
                    "platform_deal_id": "TTD-HULU-5678",
                    "activation_status": "ACTIVE",
                },
                {
                    "platform": "DV360",
                    "platform_deal_id": "DV-HULU-9012",
                    "activation_status": "PENDING",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 420000,
                "spend_to_date": 14700.00,
                "fill_rate": 0.88,
                "win_rate": 0.92,
                "avg_effective_cpm": 35.00,
                "performance_trend": "IMPROVING",
            },
        },
        # 6. CTV / PD / draft -- Peacock
        {
            "seller_url": "https://peacock.seller.example.com",
            "product_id": "peacock-ctv-sports",
            "product_name": "Peacock CTV Sports",
            "display_name": "Peacock CTV Sports Bundle",
            "deal_type": "PD",
            "status": "draft",
            "seller_deal_id": "PCOK-2026-SPT-007",
            "seller_org": "Peacock / NBCU",
            "seller_domain": "peacocktv.com",
            "seller_type": "PUBLISHER",
            "media_type": "CTV",
            "price": 42.00,
            "fixed_price_cpm": 42.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 800000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-06-30",
            "description": "Live sports and Olympics content on Peacock.",
            "_portfolio_metadata": {
                "tags": ["ctv", "sports", "olympics"],
                "advertiser_id": "ADV-COCA-COLA-005",
                "import_source": "MANUAL",
            },
        },
        # 7. CTV / PA / draft -- Pluto TV
        {
            "seller_url": "https://pluto.seller.example.com",
            "product_id": "pluto-ctv-entertainment",
            "product_name": "Pluto TV Entertainment",
            "display_name": "Pluto TV FAST Channels",
            "deal_type": "PA",
            "status": "draft",
            "seller_deal_id": "PLUTO-2026-FAST-012",
            "seller_org": "Pluto TV",
            "seller_domain": "pluto.tv",
            "seller_type": "SSP",
            "media_type": "CTV",
            "price": 12.00,
            "bid_floor_cpm": 8.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 5000000,
            "flight_start": "2026-04-15",
            "flight_end": "2026-07-31",
            "description": "FAST channel inventory across entertainment verticals.",
        },
        # 8. LINEAR_TV / PG / draft -- NBCUniversal
        {
            "seller_url": "https://nbcu.seller.example.com",
            "product_id": "nbcu-primetime-linear",
            "product_name": "NBCU Primetime Linear",
            "display_name": "NBCU Primetime Upfront 2026",
            "deal_type": "PG",
            "status": "draft",
            "seller_deal_id": "NBCU-2026-UPF-001",
            "seller_org": "NBCUniversal",
            "seller_domain": "nbcuniversal.com",
            "seller_type": "PUBLISHER",
            "media_type": "LINEAR_TV",
            "price": 45.00,
            "cpp": 45.00,
            "guaranteed_grps": 150.0,
            "price_model": "CPP",
            "currency": "USD",
            "impressions": 20000000,
            "flight_start": "2026-09-01",
            "flight_end": "2026-12-31",
            "description": "Primetime upfront commitment across NBC, USA, and Bravo.",
            "dayparts": "primetime",
            "networks": "NBC,USA,Bravo",
            "makegood_provisions": "Standard ADU makegoods within 4 weeks",
            "cancellation_window": "14 days",
            "audience_guarantee": "A18-49",
            "_portfolio_metadata": {
                "tags": ["linear", "upfront", "primetime"],
                "advertiser_id": "ADV-PG-006",
                "agency_id": "AGY-MINDSHARE-002",
                "import_source": "MANUAL",
            },
        },
        # 9. LINEAR_TV / PD / paused -- Fox
        {
            "seller_url": "https://fox.seller.example.com",
            "product_id": "fox-scatter-sports",
            "product_name": "Fox Scatter Sports",
            "display_name": "Fox Sports Scatter Q2",
            "deal_type": "PD",
            "status": "paused",
            "seller_deal_id": "FOX-2026-SCT-015",
            "seller_org": "Fox Corporation",
            "seller_domain": "fox.com",
            "seller_type": "PUBLISHER",
            "media_type": "LINEAR_TV",
            "price": 38.00,
            "cpp": 38.00,
            "price_model": "CPP",
            "currency": "USD",
            "impressions": 8000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-06-30",
            "description": "Scatter sports inventory on Fox broadcast and FS1.",
            "networks": "FOX,FS1",
            "_portfolio_metadata": {
                "tags": ["linear", "scatter", "sports"],
                "advertiser_id": "ADV-BUDWEISER-007",
                "import_source": "CSV",
            },
        },
        # 10. AUDIO / PD / active -- Spotify
        {
            "seller_url": "https://spotify.seller.example.com",
            "product_id": "spotify-podcast-audio",
            "product_name": "Spotify Podcast Audio",
            "display_name": "Spotify Podcast Network",
            "deal_type": "PD",
            "status": "paused",
            "seller_deal_id": "SPOT-2026-POD-020",
            "seller_org": "Spotify",
            "seller_domain": "spotify.com",
            "seller_type": "PUBLISHER",
            "media_type": "AUDIO",
            "price": 25.00,
            "fixed_price_cpm": 25.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 2000000,
            "flight_start": "2026-02-01",
            "flight_end": "2026-05-31",
            "description": "Premium podcast ad placements across Spotify exclusive shows.",
            "audience_segments": "podcast_listeners,music_enthusiasts",
            "_portfolio_metadata": {
                "tags": ["audio", "podcast", "premium"],
                "advertiser_id": "ADV-SAMSUNG-008",
                "import_source": "CSV",
            },
            "_activations": [
                {
                    "platform": "TTD",
                    "platform_deal_id": "TTD-SPOT-3456",
                    "activation_status": "PAUSED",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 650000,
                "spend_to_date": 16250.00,
                "fill_rate": 0.55,
                "win_rate": 0.70,
                "avg_effective_cpm": 25.00,
                "performance_trend": "DECLINING",
            },
        },
        # 11. AUDIO / PA / expired -- iHeartMedia
        {
            "seller_url": "https://iheart.seller.example.com",
            "product_id": "iheart-streaming-audio",
            "product_name": "iHeart Streaming Audio",
            "display_name": "iHeart Digital Audio PMP",
            "deal_type": "PA",
            "status": "expired",
            "seller_deal_id": "IHM-2025-Q4-030",
            "seller_org": "iHeartMedia",
            "seller_domain": "iheart.com",
            "seller_type": "PUBLISHER",
            "media_type": "AUDIO",
            "price": 10.00,
            "bid_floor_cpm": 7.50,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 4000000,
            "flight_start": "2025-10-01",
            "flight_end": "2025-12-31",
            "description": "Streaming audio across iHeart stations and podcasts.",
        },
        # 12. DOOH / PD / canceled -- Clear Channel
        {
            "seller_url": "https://clearchannel.seller.example.com",
            "product_id": "cc-dooh-airports",
            "product_name": "Clear Channel DOOH Airports",
            "display_name": "Clear Channel Airport DOOH",
            "deal_type": "PD",
            "status": "canceled",
            "seller_deal_id": "CC-2026-APT-008",
            "seller_org": "Clear Channel Outdoor",
            "seller_domain": "clearchannel.com",
            "seller_type": "SSP",
            "media_type": "DOOH",
            "price": 5.50,
            "fixed_price_cpm": 5.50,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 15000000,
            "flight_start": "2026-01-01",
            "flight_end": "2026-06-30",
            "description": "Digital screens in major US airports (JFK, LAX, ORD, ATL).",
            "geo_targets": "US-NY,US-CA,US-IL,US-GA",
            "_portfolio_metadata": {
                "tags": ["dooh", "airports", "travel"],
                "advertiser_id": "ADV-MARRIOTT-009",
                "import_source": "MANUAL",
            },
        },
        # 13. DIGITAL / PG / active -- The Washington Post
        {
            "seller_url": "https://washpost.seller.example.com",
            "product_id": "wapo-politics",
            "product_name": "WaPo Politics",
            "display_name": "Washington Post Politics PMP",
            "deal_type": "PG",
            "status": "active",
            "seller_deal_id": "WAPO-2026-POL-003",
            "seller_org": "The Washington Post",
            "seller_domain": "washingtonpost.com",
            "seller_type": "PUBLISHER",
            "media_type": "DIGITAL",
            "price": 28.00,
            "fixed_price_cpm": 28.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 1500000,
            "flight_start": "2026-01-01",
            "flight_end": "2026-04-15",
            "description": "Political news and analysis sections, high engagement.",
            "content_categories": "IAB11,IAB11-4",
            "_activations": [
                {
                    "platform": "DV360",
                    "platform_deal_id": "DV-WAPO-6789",
                    "activation_status": "ACTIVE",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 1100000,
                "spend_to_date": 30800.00,
                "fill_rate": 0.80,
                "win_rate": 0.88,
                "avg_effective_cpm": 28.00,
                "performance_trend": "STABLE",
            },
        },
        # 14. CTV / PD / active -- Roku (via Magnite SSP)
        {
            "seller_url": "https://magnite.seller.example.com",
            "product_id": "roku-magnite-ctv",
            "product_name": "Roku via Magnite CTV",
            "display_name": "Roku Channel via Magnite",
            "deal_type": "PD",
            "status": "active",
            "seller_deal_id": "MAG-ROKU-2026-009",
            "seller_org": "Magnite (Roku Channel)",
            "seller_domain": "magnite.com",
            "seller_type": "SSP",
            "media_type": "CTV",
            "price": 20.00,
            "fixed_price_cpm": 20.00,
            "bid_floor_cpm": 15.00,
            "price_model": "CPM",
            "currency": "USD",
            "impressions": 3000000,
            "flight_start": "2026-02-15",
            "flight_end": "2026-05-15",
            "description": "Roku Channel CTV supply via Magnite SSP.",
            "formats": "video_15s,video_30s",
            "schain_complete": 1,
            "schain_nodes": json.dumps(
                [
                    {"asi": "magnite.com", "sid": "12345", "hp": 1},
                    {"asi": "roku.com", "sid": "67890", "hp": 1},
                ]
            ),
            "is_direct": 0,
            "hop_count": 2,
            "_portfolio_metadata": {
                "tags": ["ctv", "ssp", "roku"],
                "advertiser_id": "ADV-GEICO-010",
                "import_source": "TTD_API",
            },
            "_activations": [
                {
                    "platform": "TTD",
                    "platform_deal_id": "TTD-ROKU-MAG-111",
                    "activation_status": "ACTIVE",
                },
            ],
            "_performance_cache": {
                "impressions_delivered": 950000,
                "spend_to_date": 19000.00,
                "fill_rate": 0.60,
                "win_rate": 0.75,
                "avg_effective_cpm": 20.00,
                "performance_trend": "STABLE",
            },
        },
    ]
