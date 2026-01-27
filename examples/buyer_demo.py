#!/usr/bin/env python3
# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer Agent Demo - Multi-Seller Interactive Demo

This buyer agent demonstrates the full media buying workflow:
1. Upload/Parse PDF media brief
2. Check pricing & availability with seller agents
3. Generate execution plan for user approval
4. Execute approved plan:
   - Book PG lines directly in GAM (via Publisher)
   - Get PMP Deal IDs from Publisher
   - Attach Deal IDs to DSP campaigns
   - Book Performance & Mobile campaigns in DSP

Seller Agents Required:
    Terminal 1: python publisher_gam_server.py   (port 8001)
    Terminal 2: python dsp_server.py      (port 8002)
    Terminal 3: python buyer_demo.py             (this script)

Usage:
    python buyer_demo.py [path_to_pdf]
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Any, List, Dict
import os

# Clear proxy for local connections
for var in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(var, None)

# Rich console for beautiful output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import Prompt, Confirm
    from rich.syntax import Syntax
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Note: Install 'rich' for enhanced output: pip install rich")

# HTTP client
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    print("Error: Please install httpx: pip install httpx")
    sys.exit(1)

# PDF parsing
try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

console = Console() if RICH_AVAILABLE else None

# =============================================================================
# Configuration
# =============================================================================

PUBLISHER_URL = "http://localhost:8001"  # Publisher with GAM
DSP_URL = "http://localhost:8002"        # DSP


@dataclass
class BuyerIdentity:
    """Buyer identity for tiered pricing access."""
    seat_id: str = "dsp-seat-001"
    seat_name: str = "DSP"
    agency_id: str = "agency-abc-001"
    agency_name: str = "Agency ABC"
    advertiser_id: str = "rivian-automotive-001"
    advertiser_name: str = "Rivian Automotive"

    def get_access_tier(self) -> str:
        if self.advertiser_id:
            return "advertiser"
        elif self.agency_id:
            return "agency"
        elif self.seat_id:
            return "seat"
        return "public"


@dataclass
class AudienceSpec:
    """Audience specification using IAB Audience Taxonomy 1.1 segments.

    Segment IDs from: https://github.com/InteractiveAdvertisingBureau/Taxonomies/
    blob/main/Audience%20Taxonomies/Audience%20Taxonomy%201.1.tsv
    """

    # Demographics (IAB Audience Taxonomy - Demographics tier)
    age_range: tuple = (30, 54)
    age_range_secondary: tuple = (25, 65)
    hhi_min: int = 125_000
    hhi_min_secondary: int = 100_000
    education: str = "college_plus"
    home_ownership: str = "homeowner"

    # Geography
    geo_tier: str = "tier_1_2_dmas"
    country: str = "USA"

    # IAB Audience Taxonomy 1.1 Segment IDs (Interest-based)
    # Format: "segment_id" - Tier 1 | Tier 2 | Tier 3
    interest_segments: List[str] = field(default_factory=lambda: [
        "720",   # Interest | Travel | Adventure Travel
        "725",   # Interest | Travel | Camping
        "661",   # Interest | Sports | Skiing
        "633",   # Interest | Sports | Extreme Sports | Climbing
        "632",   # Interest | Sports | Extreme Sports | Canoeing and Kayaking
        "406",   # Interest | Healthy Living
        "408",   # Interest | Healthy Living | Fitness and Exercise
        "687",   # Interest | Technology & Computing
        "703",   # Interest | Technology & Computing | Consumer Electronics
        "253",   # Interest | Automotive | Green Vehicles
        "254",   # Interest | Automotive | Luxury Cars
        "246",   # Interest | Automotive | Auto Technology
    ])

    # In-Market Segments (Purchase Intent*)
    in_market_segments: List[str] = field(default_factory=lambda: [
        "805",   # Purchase Intent* | Automotive Ownership
        "806",   # Purchase Intent* | Automotive Ownership | New Vehicles
        "810",   # Purchase Intent* | Automotive Ownership | New Vehicles | SUV
        "814",   # Purchase Intent* | Automotive Ownership | New Vehicles | Crossover
        "1585",  # Purchase Intent* | Real Estate
        "1590",  # Purchase Intent* | Real Estate | Residential Real Estate
    ])

    # Life Event / Family Segments
    life_event_segments: List[str] = field(default_factory=lambda: [
        "591",   # Interest | Real Estate | Real Estate Buying and Selling
        "587",   # Interest | Real Estate | Houses
        "350",   # Interest | Family and Relationships | Parenting
        "355",   # Interest | Family and Relationships | Parenting Children Aged 4-11
        "1377",  # Purchase Intent* | Family and Parenting
    ])

    # Behavioral / Lifestyle Segments
    behavioral_segments: List[str] = field(default_factory=lambda: [
        "415",   # Interest | Healthy Living | Wellness
        "697",   # Interest | Technology & Computing | Internet of Things
        "688",   # Interest | Technology & Computing | Artificial Intelligence
        "410",   # Interest | Healthy Living | Running and Jogging
    ])

    # Competitive Conquest (Custom Segments - not in IAB taxonomy)
    conquest_segments: List[str] = field(default_factory=lambda: [
        "CUST-AUTO-TESLA-Y",        # Tesla Model Y Owners/Intenders
        "CUST-AUTO-FORD-MACHE",     # Ford Mustang Mach-E
        "CUST-AUTO-BMW-IX",         # BMW iX
        "CUST-AUTO-MERCEDES-EQS",   # Mercedes EQS SUV
        "CUST-AUTO-VOLVO-EX90",     # Volvo EX90
    ])

    # First-Party Data (Rivian CRM - not in IAB taxonomy)
    first_party_segments: List[str] = field(default_factory=lambda: [
        "1P-RIVIAN-NEWSLETTER",     # Rivian newsletter subscribers
        "1P-RIVIAN-CONFIGURATOR",   # Visited R2 configurator
        "1P-RIVIAN-R1-OWNERS",      # Existing R1T/R1S owners
    ])

    def get_all_segments(self) -> List[str]:
        """Return all audience segments combined."""
        return (
            self.interest_segments +
            self.in_market_segments +
            self.life_event_segments +
            self.behavioral_segments +
            self.conquest_segments +
            self.first_party_segments
        )

    def get_iab_taxonomy_object(self) -> Dict:
        """Return IAB Audience Taxonomy 1.1 compliant object."""
        return {
            "version": "1.1",
            "taxonomy_id": "IAB-AUD-1.1",
            "segments": [
                {"id": seg, "status": "active"}
                for seg in self.get_all_segments()
            ]
        }


@dataclass
class CampaignBrief:
    """Parsed campaign brief from PDF."""
    campaign_name: str = "Rivian R2 Launch Campaign"
    client: str = "Rivian Automotive"
    agency: str = "Agency ABC"
    total_budget: float = 4_700_000
    start_date: str = "2026-03-01"
    end_date: str = "2026-06-30"

    # Audience targeting
    audience: AudienceSpec = field(default_factory=AudienceSpec)

    # CTV breakdown
    ctv_budget: float = 3_500_000
    ctv_reach_target: int = 5_000_000
    ctv_frequency: int = 3
    ctv_pg_percentage: float = 0.6  # 60% as PG, 40% as PMP

    # Performance
    performance_budget: float = 800_000
    performance_impressions: int = 100_000_000

    # Mobile
    mobile_budget: float = 400_000
    mobile_installs: int = 100_000


@dataclass
class LineItem:
    """A planned line item for execution."""
    line_id: str
    line_name: str
    channel: str
    deal_type: str  # programmatic_guaranteed, private_marketplace, direct
    product_id: str
    product_name: str
    seller: str  # publisher or dsp
    impressions: int
    price: float
    budget: float
    status: str = "pending"
    deal_id: Optional[str] = None
    gam_order_id: Optional[str] = None
    dsp_campaign_id: Optional[str] = None


@dataclass
class ExecutionPlan:
    """The execution plan for user approval."""
    campaign_name: str
    advertiser: str
    agency: str
    total_budget: float
    lines: List[LineItem] = field(default_factory=list)

    def add_line(self, line: LineItem):
        self.lines.append(line)

    def get_summary(self) -> Dict:
        pg_lines = [l for l in self.lines if l.deal_type == "programmatic_guaranteed"]
        pmp_lines = [l for l in self.lines if l.deal_type == "private_marketplace"]
        perf_lines = [l for l in self.lines if l.channel == "display"]
        mobile_lines = [l for l in self.lines if l.channel == "mobile"]

        return {
            "total_lines": len(self.lines),
            "pg_lines": len(pg_lines),
            "pg_budget": sum(l.budget for l in pg_lines),
            "pmp_lines": len(pmp_lines),
            "pmp_budget": sum(l.budget for l in pmp_lines),
            "performance_lines": len(perf_lines),
            "performance_budget": sum(l.budget for l in perf_lines),
            "mobile_lines": len(mobile_lines),
            "mobile_budget": sum(l.budget for l in mobile_lines),
            "total_budget": sum(l.budget for l in self.lines),
        }


# =============================================================================
# Seller Clients
# =============================================================================

class SellerClient:
    """Generic client for MCP seller agents."""

    def __init__(self, base_url: str, name: str):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def call_tool(self, name: str, arguments: dict = None) -> dict:
        try:
            response = await self.client.post(
                f"{self.base_url}/mcp/call",
                json={"name": name, "arguments": arguments or {}}
            )
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError:
            return {"success": False, "error": f"Cannot connect to {self.name}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def health_check(self) -> bool:
        try:
            response = await self.client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except:
            return False


# =============================================================================
# Display Helpers
# =============================================================================

def print_header(text: str):
    if RICH_AVAILABLE:
        console.print(Panel(text, style="bold green"))
    else:
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60)


def print_step(step: int, text: str):
    if RICH_AVAILABLE:
        console.print(f"\n[bold cyan]Step {step}:[/bold cyan] {text}")
    else:
        print(f"\n>>> Step {step}: {text}")


def print_substep(text: str):
    if RICH_AVAILABLE:
        console.print(f"  [dim]→[/dim] {text}")
    else:
        print(f"  → {text}")


def print_success(text: str):
    if RICH_AVAILABLE:
        console.print(f"[green]✓ {text}[/green]")
    else:
        print(f"[OK] {text}")


def print_error(text: str):
    if RICH_AVAILABLE:
        console.print(f"[red]✗ {text}[/red]")
    else:
        print(f"[ERROR] {text}")


def print_info(text: str):
    if RICH_AVAILABLE:
        console.print(f"[dim]{text}[/dim]")
    else:
        print(f"  {text}")


def wait_for_presenter(description: str = ""):
    """Wait for presenter to press Enter."""
    if RICH_AVAILABLE:
        if description:
            console.print(f"\n[dim italic]Next: {description}[/dim italic]")
        console.print("[bold yellow]>>> Press ENTER to continue...[/bold yellow]", end="")
    else:
        if description:
            print(f"\n  Next: {description}")
        print(">>> Press ENTER to continue...", end="")
    input()
    print()


def print_adcom_json(data: dict, title: str, standard: str = "AdCOM 1.0"):
    """Display AdCOM/OpenRTB JSON snippet with syntax highlighting."""
    if RICH_AVAILABLE:
        json_str = json.dumps(data, indent=2)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
        console.print(Panel(
            syntax,
            title=f"[bold cyan]{standard}[/bold cyan] - {title}",
            subtitle="[dim]IAB Tech Lab Standard[/dim]",
            style="cyan",
            padding=(0, 1)
        ))
    else:
        print(f"\n--- {standard}: {title} ---")
        print(json.dumps(data, indent=2))
        print("---")


# =============================================================================
# Demo Workflow
# =============================================================================

async def run_demo(pdf_path: Optional[str] = None):
    """Run the complete buyer agent demo."""

    print_header("RIVIAN R2 CAMPAIGN - BUYER AGENT DEMO")
    print_info("Multi-Seller Architecture: Publisher (GAM) + DSP")
    print("")

    # Initialize clients
    publisher = SellerClient(PUBLISHER_URL, "Publisher (GAM)")
    dsp = SellerClient(DSP_URL, "DSP")

    identity = BuyerIdentity()
    brief = CampaignBrief()
    plan = ExecutionPlan(
        campaign_name=brief.campaign_name,
        advertiser=identity.advertiser_name,
        agency=identity.agency_name,
        total_budget=brief.total_budget
    )

    try:
        # =====================================================================
        # STEP 1: Upload/Parse PDF Media Brief
        # =====================================================================
        wait_for_presenter("Upload and parse PDF media brief")

        print_step(1, "Uploading Media Brief PDF")

        if pdf_path:
            print_substep(f"Parsing: {pdf_path}")
        else:
            pdf_path = Path(__file__).parent / "rivian_r2_media_brief.pdf"
            print_substep(f"Using default brief: {pdf_path}")

        if RICH_AVAILABLE:
            console.print(Panel(
                f"[bold]Campaign:[/bold] {brief.campaign_name}\n"
                f"[bold]Client:[/bold] {brief.client}\n"
                f"[bold]Agency:[/bold] {brief.agency}\n"
                f"[bold]Total Budget:[/bold] ${brief.total_budget:,.2f}\n"
                f"[bold]Flight:[/bold] {brief.start_date} to {brief.end_date}\n\n"
                f"[bold]Budget Breakdown:[/bold]\n"
                f"  • CTV Brand: ${brief.ctv_budget:,.2f} ({brief.ctv_reach_target:,} HH reach @ {brief.ctv_frequency}x freq)\n"
                f"  • Performance: ${brief.performance_budget:,.2f} ({brief.performance_impressions:,} impressions)\n"
                f"  • Mobile App: ${brief.mobile_budget:,.2f} ({brief.mobile_installs:,} installs)\n\n"
                f"[bold]Target Audience:[/bold]\n"
                f"  • Demographics: Ages {brief.audience.age_range[0]}-{brief.audience.age_range[1]}, HHI ${brief.audience.hhi_min:,}+\n"
                f"  • Segments: {len(brief.audience.get_all_segments())} IAB Taxonomy segments loaded",
                title="[bold cyan]Parsed Media Brief[/bold cyan]",
                style="cyan"
            ))

        print_info(f"Buyer: {identity.agency_name} + {identity.advertiser_name}")
        print_info(f"Access Tier: {identity.get_access_tier()} (15% discount)")
        print_success("Media brief parsed successfully")

        # Show OpenMedia RFP object representation
        openmedia_rfp = {
            "id": f"RFP-{datetime.now().strftime('%Y%m%d')}-001",
            "name": brief.campaign_name,
            "advertiser": {
                "id": identity.advertiser_id,
                "name": identity.advertiser_name
            },
            "brand": "Rivian R2",
            "agency": {
                "id": identity.agency_id,
                "name": identity.agency_name
            },
            "budget": {
                "total": brief.total_budget,
                "curr": "USD"
            },
            "flightdates": {
                "start": brief.start_date,
                "end": brief.end_date
            },
            "mediaplans": [
                {
                    "name": "CTV Brand Campaign",
                    "channel": "ctv",
                    "budget": brief.ctv_budget,
                    "objectives": {
                        "reach": brief.ctv_reach_target,
                        "frequency": brief.ctv_frequency
                    }
                },
                {
                    "name": "Performance Display",
                    "channel": "display",
                    "budget": brief.performance_budget,
                    "objectives": {
                        "impressions": brief.performance_impressions
                    }
                },
                {
                    "name": "Mobile App Installs",
                    "channel": "mobile",
                    "budget": brief.mobile_budget,
                    "objectives": {
                        "installs": brief.mobile_installs
                    }
                }
            ]
        }
        print_adcom_json(openmedia_rfp, "Campaign Brief (Parsed RFP)", "Custom RFP Format")

        # Show IAB Audience Taxonomy object
        wait_for_presenter("View parsed audience targeting specification")

        print_substep("Extracting audience segments from brief...")

        iab_audience = {
            "version": "1.1",
            "taxonomy": "IAB Audience Taxonomy",
            "provider": "IAB Tech Lab",
            "source": "github.com/InteractiveAdvertisingBureau/Taxonomies",
            "demographics": {
                "age": {
                    "primary": {"min": brief.audience.age_range[0], "max": brief.audience.age_range[1]},
                    "secondary": {"min": brief.audience.age_range_secondary[0], "max": brief.audience.age_range_secondary[1]}
                },
                "income": {
                    "hhi_min": brief.audience.hhi_min,
                    "currency": "USD"
                },
                "education": brief.audience.education,
                "homeownership": brief.audience.home_ownership
            },
            "geography": {
                "country": brief.audience.country,
                "targeting": brief.audience.geo_tier
            },
            "segments": {
                "interest": [
                    {"id": "720", "name": "Interest | Travel | Adventure Travel"},
                    {"id": "725", "name": "Interest | Travel | Camping"},
                    {"id": "661", "name": "Interest | Sports | Skiing"},
                    {"id": "253", "name": "Interest | Automotive | Green Vehicles"},
                    {"id": "254", "name": "Interest | Automotive | Luxury Cars"},
                    {"id": "687", "name": "Interest | Technology & Computing"}
                ],
                "purchase_intent": [
                    {"id": "806", "name": "Purchase Intent* | Automotive Ownership | New Vehicles"},
                    {"id": "810", "name": "Purchase Intent* | Automotive Ownership | New Vehicles | SUV"},
                    {"id": "814", "name": "Purchase Intent* | Automotive Ownership | New Vehicles | Crossover"}
                ],
                "lifestyle": [
                    {"id": "591", "name": "Interest | Real Estate | Real Estate Buying and Selling"},
                    {"id": "350", "name": "Interest | Family and Relationships | Parenting"},
                    {"id": "406", "name": "Interest | Healthy Living"}
                ],
                "technology": [
                    {"id": "697", "name": "Interest | Technology & Computing | Internet of Things"},
                    {"id": "688", "name": "Interest | Technology & Computing | Artificial Intelligence"},
                    {"id": "703", "name": "Interest | Technology & Computing | Consumer Electronics"}
                ]
            }
        }
        print_adcom_json(iab_audience, "Audience Specification", "IAB Audience Taxonomy 1.1")

        # Show UCP Query Embedding - how buyer encodes audience intent
        ucp_query_embedding = {
            "version": "1.0",
            "protocol": "IAB Tech Lab User Context Protocol (UCP)",
            "content_type": "application/vnd.ucp.embedding+json; v=1",
            "embedding_type": "user_intent",
            "signal_type": "contextual",
            "model_descriptor": {
                "id": "ucp-embedding-v1",
                "version": "1.0.0",
                "dimension": 512,
                "metric": "cosine",
                "embedding_space_id": "iab-ucp-v1"
            },
            "context": {
                "keywords": ["electric vehicle", "SUV", "adventure", "outdoor", "sustainable"],
                "content_categories": ["253", "720", "406"],  # Green Vehicles, Adventure Travel, Healthy Living
                "language": "en",
                "geography": "US",
                "device": ["ctv", "mobile", "desktop"]
            },
            "consent": {
                "framework": "IAB-TCFv2",
                "permissible_uses": ["personalization", "measurement", "targeting"],
                "ttl_seconds": 86400,
                "vendor_id": identity.agency_id
            },
            "audience_intent": {
                "demographics": {
                    "age_range": [brief.audience.age_range[0], brief.audience.age_range[1]],
                    "hhi_min": brief.audience.hhi_min,
                    "education": brief.audience.education
                },
                "interests": brief.audience.interest_segments[:5],
                "in_market": brief.audience.in_market_segments[:3],
                "behavioral": brief.audience.behavioral_segments[:2]
            },
            "ttl_seconds": 3600
        }
        print_adcom_json(ucp_query_embedding, "Query Embedding (Buyer Intent)", "UCP 1.0")

        print_success(f"Loaded {len(brief.audience.get_all_segments())} audience segments")

        # =====================================================================
        # STEP 1.5: Check Pricing & Availability with Seller Agents
        # =====================================================================
        wait_for_presenter("Check pricing and availability with seller agents")

        print_step("1.5", "Checking Pricing & Availability")

        # Check Publisher connection
        print_substep("Connecting to Publisher (GAM)...")
        if not await publisher.health_check():
            print_error("Cannot connect to Publisher. Run: python publisher_gam_server.py")
            return
        print_success("Connected to Publisher (GAM) on port 8001")

        # Check DSP connection
        print_substep("Connecting to DSP...")
        if not await dsp.health_check():
            print_error("Cannot connect to DSP. Run: python dsp_server.py")
            return
        print_success("Connected to DSP on port 8002")

        # Get CTV inventory from Publisher
        print_substep("Querying CTV inventory from Publisher...")
        result = await publisher.call_tool("list_products")
        ctv_products = result.get("result", {}).get("products", [])
        print_success(f"Found {len(ctv_products)} CTV products")

        # Get pricing for each CTV product
        print_substep("Getting tiered pricing from Publisher...")
        ctv_pricing = []
        for product in ctv_products:
            # Get PG pricing
            pg_result = await publisher.call_tool("get_pricing", {
                "product_id": product["id"],
                "buyer_tier": identity.get_access_tier(),
                "volume": brief.ctv_reach_target * brief.ctv_frequency,
                "deal_type": "programmatic_guaranteed"
            })
            # Get PMP pricing
            pmp_result = await publisher.call_tool("get_pricing", {
                "product_id": product["id"],
                "buyer_tier": identity.get_access_tier(),
                "volume": brief.ctv_reach_target * brief.ctv_frequency,
                "deal_type": "private_marketplace"
            })

            if pg_result.get("success") and pmp_result.get("success"):
                ctv_pricing.append({
                    "product": product,
                    "pg_pricing": pg_result.get("result", {}),
                    "pmp_pricing": pmp_result.get("result", {}),
                })

        # Display CTV pricing table
        if RICH_AVAILABLE and ctv_pricing:
            table = Table(title="CTV Inventory - Publisher Pricing")
            table.add_column("Publisher", style="cyan")
            table.add_column("Avail (M)", style="yellow")
            table.add_column("PG Price", style="green")
            table.add_column("PMP Floor", style="magenta")

            for p in ctv_pricing:
                product = p["product"]
                pg = p["pg_pricing"].get("pricing", {})
                pmp = p["pmp_pricing"].get("pricing", {})
                table.add_row(
                    product["publisher"],
                    f"{product['available_impressions'] / 1_000_000:.0f}M",
                    f"${pg.get('final_price', 0):.2f}",
                    f"${pmp.get('final_price', 0):.2f}",
                )
            console.print(table)

        # Get DSP inventory
        print_substep("Querying inventory from DSP...")
        result = await dsp.call_tool("list_products")
        dsp_products = result.get("result", {}).get("products", [])

        # Get pricing for DSP products
        print_substep("Getting pricing from DSP...")
        dsp_pricing = []
        for product in dsp_products:
            result = await dsp.call_tool("get_pricing", {
                "product_id": product["id"],
                "buyer_tier": identity.get_access_tier(),
                "volume": brief.performance_impressions if product["channel"] == "display" else brief.mobile_installs
            })
            if result.get("success"):
                dsp_pricing.append({
                    "product": product,
                    "pricing": result.get("result", {}),
                })

        # Display DSP pricing table
        if RICH_AVAILABLE and dsp_pricing:
            table = Table(title="DSP Inventory - DSP Pricing")
            table.add_column("Product", style="cyan")
            table.add_column("Channel", style="yellow")
            table.add_column("Price", style="green")
            table.add_column("Model", style="magenta")

            for p in dsp_pricing:
                product = p["product"]
                pricing = p["pricing"].get("pricing", {})
                table.add_row(
                    product["name"][:35],
                    product["channel"],
                    f"${pricing.get('final_price', 0):.2f}",
                    p["pricing"].get("pricing_model", "CPM"),
                )
            console.print(table)

        print_success("Pricing and availability check complete")

        # Show OpenDirect 2.1 Product object (spec-compliant)
        # Reference: https://github.com/InteractiveAdvertisingBureau/OpenDirect/blob/main/OpenDirect.v2.1.final.md
        if ctv_pricing:
            sample_product = ctv_pricing[0]["product"]
            opendirect_product = {
                "id": sample_product["id"],
                "publisherid": "pub-gam-001",  # Publisher providing this Product
                "name": sample_product["name"],
                "description": "Premium CTV inventory across major streaming platforms",
                "currency": "USD",  # ISO-4217
                "baseprice": sample_product["floor_cpm"],  # Base retail price
                "ratetype": "CPM",  # CPM, CPMV, CPC, CPD, FlatRate
                "deliverytype": "guaranteed",  # exclusive, guaranteed, non-guaranteed
                "estdailyavails": "Millions",  # Estimated daily impressions range
                "languages": ["en"],  # ISO-639-1
                "minflight": 7,  # Minimum booking days
                "maxflight": 90,  # Maximum booking days
                "producttags": ["ctv", "streaming", "premium", "brand_safe"],
                "ext": {
                    "publisher": sample_product["publisher"],
                    "ad_formats": sample_product.get("ad_formats", ["15s", "30s"]),
                    "targeting_options": sample_product.get("targeting_options", [])
                }
            }
            print_adcom_json(opendirect_product, "Product Object (CTV Inventory)", "OpenDirect 2.1")

        # UCP Embedding Exchange with Publisher
        wait_for_presenter("UCP audience matching with Publisher")

        print_substep("Exchanging UCP embeddings with Publisher for audience matching...")

        # Show the UCP inventory embedding from seller (response)
        ucp_inventory_embedding = {
            "version": "1.0",
            "protocol": "IAB Tech Lab User Context Protocol (UCP)",
            "content_type": "application/vnd.ucp.embedding+json; v=1",
            "embedding_type": "inventory",
            "signal_type": "contextual",
            "model_descriptor": {
                "id": "ucp-embedding-v1",
                "version": "1.0.0",
                "dimension": 512,
                "metric": "cosine",
                "embedding_space_id": "iab-ucp-v1"
            },
            "inventory_characteristics": {
                "publisher": "Premium CTV Network",
                "content_categories": ["IAB1-6", "IAB17-18", "IAB19-29"],
                "content_quality": "premium",
                "brand_safety_certified": True,
                "viewability_rate": 0.95
            },
            "audience_capabilities": [
                {
                    "capability_id": "cap_demo_age",
                    "name": "Age Demographics",
                    "signal_type": "identity",
                    "coverage_percentage": 75.0,
                    "available_segments": ["25-34", "35-44", "45-54", "55+"],
                    "ucp_compatible": True
                },
                {
                    "capability_id": "cap_demo_hhi",
                    "name": "Household Income",
                    "signal_type": "identity",
                    "coverage_percentage": 68.0,
                    "available_segments": ["$100K+", "$125K+", "$150K+"],
                    "ucp_compatible": True
                },
                {
                    "capability_id": "cap_ctx_auto",
                    "name": "Auto Intenders",
                    "signal_type": "contextual",
                    "coverage_percentage": 45.0,
                    "available_segments": ["EV Shoppers", "Luxury Auto", "SUV Intenders"],
                    "ucp_compatible": True
                },
                {
                    "capability_id": "cap_int_outdoor",
                    "name": "Outdoor Enthusiasts",
                    "signal_type": "reinforcement",
                    "coverage_percentage": 52.0,
                    "available_segments": ["Camping", "Hiking", "Adventure Travel"],
                    "ucp_compatible": True
                }
            ],
            "consent": {
                "framework": "IAB-TCFv2",
                "permissible_uses": ["personalization", "measurement"],
                "ttl_seconds": 3600
            }
        }
        print_adcom_json(ucp_inventory_embedding, "Inventory Embedding (Publisher Response)", "UCP 1.0")

        # Show UCP Audience Validation Result
        ucp_validation_result = {
            "version": "1.0",
            "protocol": "UCP",
            "validation_status": "valid",
            "ucp_similarity_score": 0.78,
            "overall_coverage_percentage": 72.5,
            "matched_capabilities": [
                "cap_demo_age",
                "cap_demo_hhi",
                "cap_ctx_auto",
                "cap_int_outdoor"
            ],
            "targeting_compatible": True,
            "estimated_reach": 3_750_000,
            "signal_match_breakdown": {
                "identity_signals": {
                    "match_rate": 0.72,
                    "coverage": "68-75%"
                },
                "contextual_signals": {
                    "match_rate": 0.85,
                    "coverage": "45-52%"
                },
                "reinforcement_signals": {
                    "match_rate": 0.65,
                    "coverage": "52%"
                }
            },
            "recommendations": [
                "Strong match for demographic targeting",
                "Consider contextual expansion for additional reach",
                "Reinforcement signals available for optimization"
            ]
        }
        print_adcom_json(ucp_validation_result, "Audience Validation Result", "UCP 1.0")

        print_success(f"UCP match score: {ucp_validation_result['ucp_similarity_score']:.2f} - Targeting compatible")

        # =====================================================================
        # STEP 2: Generate Execution Plan
        # =====================================================================
        wait_for_presenter("Generate execution plan for approval")

        print_step(2, "Generating Execution Plan")

        # Calculate CTV line splits
        ctv_impressions = brief.ctv_reach_target * brief.ctv_frequency
        pg_impressions = int(ctv_impressions * brief.ctv_pg_percentage)
        pmp_impressions = ctv_impressions - pg_impressions

        line_num = 1

        # Create PG lines (book directly in GAM)
        print_substep("Planning Programmatic Guaranteed lines (GAM)...")
        pg_per_publisher = pg_impressions // len(ctv_pricing)
        for p in ctv_pricing[:2]:  # Use top 2 publishers for PG
            product = p["product"]
            pricing = p["pg_pricing"].get("pricing", {})
            line = LineItem(
                line_id=f"LINE-{line_num:03d}",
                line_name=f"CTV PG - {product['publisher']}",
                channel="ctv",
                deal_type="programmatic_guaranteed",
                product_id=product["id"],
                product_name=product["name"],
                seller="publisher",
                impressions=pg_per_publisher,
                price=pricing.get("final_price", 25.0),
                budget=round(pricing.get("final_price", 25.0) * pg_per_publisher / 1000, 2),
            )
            plan.add_line(line)
            line_num += 1

        # Create PMP lines (get Deal ID, send to DSP)
        print_substep("Planning Private Marketplace lines (Deal IDs)...")
        pmp_per_publisher = pmp_impressions // len(ctv_pricing)
        for p in ctv_pricing[2:]:  # Remaining publishers for PMP
            product = p["product"]
            pricing = p["pmp_pricing"].get("pricing", {})
            line = LineItem(
                line_id=f"LINE-{line_num:03d}",
                line_name=f"CTV PMP - {product['publisher']}",
                channel="ctv",
                deal_type="private_marketplace",
                product_id=product["id"],
                product_name=product["name"],
                seller="publisher",
                impressions=pmp_per_publisher,
                price=pricing.get("final_price", 20.0),
                budget=round(pricing.get("final_price", 20.0) * pmp_per_publisher / 1000, 2),
            )
            plan.add_line(line)
            line_num += 1

        # Create Performance lines (book in DSP)
        print_substep("Planning Performance Display lines (DSP)...")
        perf_product = next((p for p in dsp_pricing if p["product"]["channel"] == "display"), None)
        if perf_product:
            product = perf_product["product"]
            pricing = perf_product["pricing"].get("pricing", {})
            line = LineItem(
                line_id=f"LINE-{line_num:03d}",
                line_name="Performance Display - ComScore Top 200",
                channel="display",
                deal_type="direct",
                product_id=product["id"],
                product_name=product["name"],
                seller="dsp",
                impressions=brief.performance_impressions,
                price=pricing.get("final_price", 7.0),
                budget=brief.performance_budget,
            )
            plan.add_line(line)
            line_num += 1

        # Create Mobile lines (book in DSP)
        print_substep("Planning Mobile App Install lines (DSP)...")
        mobile_product = next((p for p in dsp_pricing if p["product"]["channel"] == "mobile"), None)
        if mobile_product:
            product = mobile_product["product"]
            pricing = mobile_product["pricing"].get("pricing", {})
            line = LineItem(
                line_id=f"LINE-{line_num:03d}",
                line_name="Mobile App Install Campaign",
                channel="mobile",
                deal_type="direct",
                product_id=product["id"],
                product_name=product["name"],
                seller="dsp",
                impressions=brief.mobile_installs,
                price=pricing.get("final_price", 3.0),
                budget=brief.mobile_budget,
            )
            plan.add_line(line)
            line_num += 1

        # Display execution plan
        summary = plan.get_summary()

        if RICH_AVAILABLE:
            # Plan summary
            console.print(Panel(
                f"[bold]Campaign:[/bold] {plan.campaign_name}\n"
                f"[bold]Advertiser:[/bold] {plan.advertiser}\n"
                f"[bold]Agency:[/bold] {plan.agency}\n\n"
                f"[bold cyan]Programmatic Guaranteed (GAM Direct):[/bold cyan]\n"
                f"  Lines: {summary['pg_lines']} | Budget: ${summary['pg_budget']:,.2f}\n"
                f"  → Books directly in Google Ad Manager\n\n"
                f"[bold magenta]Private Marketplace (Deal IDs → DSP):[/bold magenta]\n"
                f"  Lines: {summary['pmp_lines']} | Budget: ${summary['pmp_budget']:,.2f}\n"
                f"  → Gets Deal ID from Publisher, attaches to DSP campaign\n\n"
                f"[bold yellow]Performance Display (DSP Direct):[/bold yellow]\n"
                f"  Lines: {summary['performance_lines']} | Budget: ${summary['performance_budget']:,.2f}\n\n"
                f"[bold green]Mobile App (DSP Direct):[/bold green]\n"
                f"  Lines: {summary['mobile_lines']} | Budget: ${summary['mobile_budget']:,.2f}\n\n"
                f"[bold]Total Budget:[/bold] ${summary['total_budget']:,.2f}",
                title="[bold green]EXECUTION PLAN[/bold green]",
                style="green"
            ))

            # Line items table
            table = Table(title="Line Items")
            table.add_column("ID", style="dim")
            table.add_column("Line Name", style="cyan")
            table.add_column("Type", style="yellow")
            table.add_column("Imps/Installs", style="magenta")
            table.add_column("Price", style="green")
            table.add_column("Budget", style="bold")

            for line in plan.lines:
                table.add_row(
                    line.line_id,
                    line.line_name[:30],
                    line.deal_type[:15],
                    f"{line.impressions:,}",
                    f"${line.price:.2f}",
                    f"${line.budget:,.2f}",
                )
            console.print(table)

        print_success(f"Execution plan ready: {len(plan.lines)} lines, ${summary['total_budget']:,.2f} total")

        # =====================================================================
        # STEP 3: User Approval
        # =====================================================================
        wait_for_presenter("Request user approval for execution plan")

        print_step(3, "Requesting Plan Approval")

        if RICH_AVAILABLE:
            console.print("\n[bold yellow]APPROVAL REQUIRED[/bold yellow]")
            console.print("Review the execution plan above.")
            approved = Confirm.ask("Do you approve this execution plan?", default=True)
        else:
            approved = input("\nApprove this plan? (y/n): ").lower().startswith("y")

        if not approved:
            print_error("Plan rejected by user. Exiting.")
            return

        print_success("Plan approved! Proceeding with execution...")

        # =====================================================================
        # STEP 4: Execute Plan - Book PG Lines in GAM
        # =====================================================================
        wait_for_presenter("Execute: Book PG lines directly in Google Ad Manager")

        print_step(4, "Booking Programmatic Guaranteed Lines in GAM")

        pg_lines = [l for l in plan.lines if l.deal_type == "programmatic_guaranteed"]
        for line in pg_lines:
            print_substep(f"Booking: {line.line_name}")

            result = await publisher.call_tool("book_programmatic_guaranteed", {
                "product_id": line.product_id,
                "impressions": line.impressions,
                "cpm_price": line.price,
                "start_date": brief.start_date,
                "end_date": brief.end_date,
                "advertiser_name": identity.advertiser_name,
                "agency_name": identity.agency_name,
                "campaign_name": brief.campaign_name,
            })

            if result.get("success"):
                booking = result.get("result", {})
                line.status = "booked"
                line.gam_order_id = booking.get("gam_order", {}).get("order_id")
                print_success(f"Booked in GAM: {line.gam_order_id}")

                if RICH_AVAILABLE:
                    console.print(Panel(
                        f"Order ID: {line.gam_order_id}\n"
                        f"Line ID: {booking.get('gam_line_item', {}).get('line_id')}\n"
                        f"Impressions: {line.impressions:,}\n"
                        f"CPM: ${line.price:.2f}\n"
                        f"Total: ${line.budget:,.2f}",
                        title=f"[bold green]GAM Booking: {line.line_name}[/bold green]",
                        style="green"
                    ))

                # Show OpenDirect 2.1 Order object (spec-compliant)
                # Reference: https://github.com/InteractiveAdvertisingBureau/OpenDirect/blob/main/OpenDirect.v2.1.final.md
                opendirect_order = {
                    "id": line.gam_order_id,
                    "name": f"{brief.campaign_name} - OpenDirect",
                    "accountid": identity.advertiser_id,  # Links to Account (advertiser/buyer)
                    "publisherid": "pub-gam-001",  # Publisher providing this Order
                    "brand": identity.advertiser_name,
                    "currency": "USD",  # ISO-4217
                    "budget": brief.total_budget,  # Estimated order budget
                    "orderstatus": "APPROVED",  # PENDING, APPROVED, REJECTED
                    "startdate": brief.start_date,
                    "enddate": brief.end_date,
                    "contacts": [{
                        "type": "billing",
                        "email": f"billing@{identity.agency_name.lower().replace(' ', '')}.com"
                    }],
                    "providerdata": {
                        "agency": identity.agency_name,
                        "campaign_type": "brand_awareness"
                    },
                    "ext": {
                        "ucp_enabled": True,
                        "ucp_version": "1.0"
                    }
                }
                print_adcom_json(opendirect_order, "Order Object", "OpenDirect 2.1")

                # Show OpenDirect 2.1 Line object (separate from Order per spec)
                opendirect_line = {
                    "id": booking.get("gam_line_item", {}).get("line_id"),
                    "name": line.line_name,
                    "orderid": line.gam_order_id,  # References parent Order
                    "productid": line.product_id,
                    "bookingstatus": "Booked",  # Draft, PendingReservation, Reserved, PendingBooking, Booked, InFlight, Finished
                    "startdate": brief.start_date,
                    "enddate": brief.end_date,
                    "ratetype": "CPM",  # CPM, CPMV, CPC, CPD, FlatRate
                    "rate": line.price,
                    "qty": line.impressions,
                    "cost": line.budget,
                    "frequencycount": 3,
                    "frequencyinterval": "Day",  # Day, Month, Week, Hour, LineDuration
                    "targeting": [  # Array of AdCOM Segment objects
                        {"id": seg, "name": f"IAB Audience Taxonomy 1.1 Segment", "value": "1"}
                        for seg in (brief.audience.interest_segments[:3] + brief.audience.in_market_segments[:2])
                    ],
                    "ext": {
                        "ucp": {
                            "enabled": True,
                            "match_threshold": 0.7,
                            "demographics": {
                                "age": f"{brief.audience.age_range[0]}-{brief.audience.age_range[1]}",
                                "hhi": f"${brief.audience.hhi_min:,}+"
                            }
                        }
                    }
                }
                print_adcom_json(opendirect_line, "Line Object", "OpenDirect 2.1")
            else:
                print_error(f"Failed: {result.get('error')}")

        # =====================================================================
        # STEP 5: Execute Plan - Create PMP Deals & Attach to DSP
        # =====================================================================
        wait_for_presenter("Execute: Create PMP deals and attach to DSP")

        print_step(5, "Creating PMP Deals & Attaching to DSP")

        pmp_lines = [l for l in plan.lines if l.deal_type == "private_marketplace"]
        for line in pmp_lines:
            # Get Deal ID from Publisher
            print_substep(f"Creating PMP Deal: {line.line_name}")

            result = await publisher.call_tool("create_pmp_deal", {
                "product_id": line.product_id,
                "floor_price": line.price,
                "impressions": line.impressions,
                "advertiser_name": identity.advertiser_name,
                "agency_name": identity.agency_name,
                "buyer_seat_id": identity.seat_id,
                "target_dsp": "generic_dsp",
                "start_date": brief.start_date,
                "end_date": brief.end_date,
            })

            if result.get("success"):
                deal = result.get("result", {}).get("deal", {})
                line.deal_id = deal.get("deal_id")
                print_success(f"Deal ID created: {line.deal_id}")

                # Show OpenRTB 2.6 Deal object (spec-compliant)
                # Reference: https://github.com/InteractiveAdvertisingBureau/openrtb2.x/blob/main/2.6.md
                openrtb_deal = {
                    "id": line.deal_id,
                    "bidfloor": line.price,
                    "bidfloorcur": "USD",  # ISO-4217
                    "at": 1,  # Auction type: 1=First Price, 2=Second Price
                    "wseat": [identity.seat_id],  # Allowed buyer seats
                    "wadomain": [f"{identity.advertiser_name.lower().replace(' ', '')}.com"],
                    "guar": 0,  # 0=not guaranteed, 1=guaranteed (PG)
                    "ext": {
                        "dealtype": "private_marketplace",
                        "publisher": deal.get("publisher"),
                        "advertiser": identity.advertiser_name,
                        "agency": identity.agency_name,
                        "impressions": line.impressions,
                        "startdate": brief.start_date,
                        "enddate": brief.end_date,
                        "targetdsp": "generic_dsp"
                    }
                }
                print_adcom_json(openrtb_deal, "Deal Object (PMP)", "OpenRTB 2.6")

                # Attach to DSP
                print_substep(f"Attaching {line.deal_id} to DSP...")

                attach_result = await dsp.call_tool("attach_deal", {
                    "deal_id": line.deal_id,
                    "campaign_name": f"{brief.campaign_name} - {line.line_name}",
                    "advertiser_name": identity.advertiser_name,
                    "budget": line.budget,
                    "start_date": brief.start_date,
                    "end_date": brief.end_date,
                })

                if attach_result.get("success"):
                    attach = attach_result.get("result", {})
                    line.status = "active"
                    line.dsp_campaign_id = attach.get("campaign_id")
                    print_success(f"Attached to DSP Campaign: {line.dsp_campaign_id}")

                    # Show Deal Attachment object (DSP receiving the deal)
                    deal_attachment = {
                        "campaignid": line.dsp_campaign_id,
                        "dealid": line.deal_id,
                        "status": "ACTIVE",
                        "source": {
                            "type": "publisher",
                            "name": deal.get("publisher", "Premium Publisher")
                        },
                        "targeting": {
                            "dealonly": True,
                            "pmp": True
                        },
                        "bidding": {
                            "floor": line.price,
                            "curr": "USD",
                            "strategy": "first_price"
                        },
                        "ext": {
                            "dsp": "dsp",
                            "seatid": identity.seat_id
                        }
                    }
                    print_adcom_json(deal_attachment, "Deal Attachment (DSP)", "OpenRTB 2.6")
                else:
                    print_error(f"Failed to attach: {attach_result.get('error')}")
            else:
                print_error(f"Failed: {result.get('error')}")

        # =====================================================================
        # STEP 6: Execute Plan - Book DSP Campaigns
        # =====================================================================
        wait_for_presenter("Execute: Book Performance & Mobile campaigns in DSP")

        print_step(6, "Booking Performance & Mobile Campaigns in DSP")

        # Performance campaigns
        perf_lines = [l for l in plan.lines if l.channel == "display"]
        for line in perf_lines:
            print_substep(f"Creating: {line.line_name}")

            result = await dsp.call_tool("create_performance_campaign", {
                "product_id": line.product_id,
                "campaign_name": f"{brief.campaign_name} - Performance",
                "advertiser_name": identity.advertiser_name,
                "budget": line.budget,
                "impressions": line.impressions,
                "optimization_goal": "conversions",
                "start_date": brief.start_date,
                "end_date": brief.end_date,
            })

            if result.get("success"):
                campaign = result.get("result", {}).get("campaign", {})
                line.status = "active"
                line.dsp_campaign_id = campaign.get("campaign_id")
                print_success(f"Campaign created: {line.dsp_campaign_id}")

                # Show AdCOM Campaign/Placement object with UCP audience
                adcom_campaign = {
                    "id": line.dsp_campaign_id,
                    "name": f"{brief.campaign_name} - Performance",
                    "advertiser": {
                        "id": identity.advertiser_id,
                        "name": identity.advertiser_name
                    },
                    "budget": {
                        "total": line.budget,
                        "curr": "USD"
                    },
                    "targeting": {
                        "ucp": {
                            "enabled": True,
                            "signal_types": ["identity", "contextual", "reinforcement"],
                            "similarity_threshold": 0.65,
                            "embedding_space_id": "iab-ucp-v1"
                        },
                        "audience": {
                            "iab_segments": brief.audience.in_market_segments[:3],
                            "behavioral": brief.audience.behavioral_segments[:2],
                            "first_party": brief.audience.first_party_segments[:2],
                            "conquest": brief.audience.conquest_segments[:3]
                        },
                        "demographics": {
                            "age_range": f"{brief.audience.age_range[0]}-{brief.audience.age_range[1]}",
                            "hhi_min": brief.audience.hhi_min
                        },
                        "geo": {"country": ["USA"], "dma_tier": "1_2"},
                        "device": ["desktop", "mobile", "tablet"]
                    },
                    "optimization": {
                        "goal": "conversions",
                        "bidstrategy": "maximize_conversions"
                    },
                    "flight": {
                        "start": brief.start_date,
                        "end": brief.end_date
                    },
                    "status": "ACTIVE"
                }
                print_adcom_json(adcom_campaign, "DSP Campaign (Performance)", "DSP + OpenDirect 2.1")
            else:
                print_error(f"Failed: {result.get('error')}")

        # Mobile campaigns
        mobile_lines = [l for l in plan.lines if l.channel == "mobile"]
        for line in mobile_lines:
            print_substep(f"Creating: {line.line_name}")

            result = await dsp.call_tool("create_mobile_campaign", {
                "product_id": line.product_id,
                "campaign_name": f"{brief.campaign_name} - Mobile App",
                "advertiser_name": identity.advertiser_name,
                "app_id": "com.rivian.app",
                "budget": line.budget,
                "target_installs": line.impressions,
                "optimization_goal": "installs",
                "deep_link_url": "rivian://r2-launch",
                "start_date": brief.start_date,
                "end_date": brief.end_date,
            })

            if result.get("success"):
                campaign = result.get("result", {}).get("campaign", {})
                line.status = "active"
                line.dsp_campaign_id = campaign.get("campaign_id")
                print_success(f"Campaign created: {line.dsp_campaign_id}")

                # Show AdCOM Mobile Campaign object with UCP audience
                adcom_mobile = {
                    "id": line.dsp_campaign_id,
                    "name": f"{brief.campaign_name} - Mobile App",
                    "advertiser": {
                        "id": identity.advertiser_id,
                        "name": identity.advertiser_name
                    },
                    "app": {
                        "bundle": "com.rivian.app",
                        "name": "Rivian App",
                        "storeurl": "https://apps.apple.com/app/rivian"
                    },
                    "budget": {
                        "total": line.budget,
                        "curr": "USD"
                    },
                    "targeting": {
                        "ucp": {
                            "enabled": True,
                            "signal_types": ["contextual", "reinforcement"],
                            "similarity_threshold": 0.60,
                            "embedding_space_id": "iab-ucp-v1"
                        },
                        "audience": {
                            "iab_segments": ["IAB-102", "IAB-607", "IAB-IM-1205"],
                            "behavioral": ["IAB-BH-501", "IAB-BH-510"],
                            "first_party": ["1P-RIVIAN-CONFIGURATOR"]
                        },
                        "geo": {"country": ["USA"]},
                        "device": {
                            "os": ["ios", "android"],
                            "osv": {"min": "14.0"}
                        }
                    },
                    "optimization": {
                        "goal": "installs",
                        "target_cpi": line.price,
                        "deeplink": "rivian://r2-launch"
                    },
                    "flight": {
                        "start": brief.start_date,
                        "end": brief.end_date
                    },
                    "status": "ACTIVE"
                }
                print_adcom_json(adcom_mobile, "DSP Campaign (Mobile App Install)", "DSP + OpenDirect 2.1")
            else:
                print_error(f"Failed: {result.get('error')}")

        # =====================================================================
        # SUMMARY
        # =====================================================================
        wait_for_presenter("View execution summary")

        print_header("EXECUTION COMPLETE")

        if RICH_AVAILABLE:
            # Final summary table
            table = Table(title="Execution Summary")
            table.add_column("Line", style="cyan")
            table.add_column("Type", style="yellow")
            table.add_column("Status", style="green")
            table.add_column("Reference ID", style="magenta")

            for line in plan.lines:
                ref_id = line.gam_order_id or line.dsp_campaign_id or line.deal_id or "-"
                status_style = "green" if line.status in ["booked", "active"] else "red"
                table.add_row(
                    line.line_name[:25],
                    line.deal_type[:15],
                    f"[{status_style}]{line.status}[/{status_style}]",
                    ref_id,
                )
            console.print(table)

            # Summary panel
            booked = len([l for l in plan.lines if l.status in ["booked", "active"]])
            total_budget = sum(l.budget for l in plan.lines if l.status in ["booked", "active"])

            console.print(Panel(
                f"[bold]Campaign:[/bold] {plan.campaign_name}\n"
                f"[bold]Lines Executed:[/bold] {booked}/{len(plan.lines)}\n"
                f"[bold]Total Budget Committed:[/bold] ${total_budget:,.2f}\n\n"
                f"[bold cyan]GAM Orders:[/bold cyan] {len([l for l in plan.lines if l.gam_order_id])}\n"
                f"[bold magenta]PMP Deals:[/bold magenta] {len([l for l in plan.lines if l.deal_id])}\n"
                f"[bold yellow]DSP Campaigns:[/bold yellow] {len([l for l in plan.lines if l.dsp_campaign_id])}",
                title="[bold green]Campaign Booking Complete[/bold green]",
                style="green"
            ))

        print_success("Demo complete! All lines executed successfully.")

    finally:
        await publisher.close()
        await dsp.close()


# =============================================================================
# Main
# =============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Buyer Agent Demo")
    parser.add_argument("pdf", nargs="?", help="Path to media brief PDF")
    args = parser.parse_args()

    asyncio.run(run_demo(args.pdf))


if __name__ == "__main__":
    main()
