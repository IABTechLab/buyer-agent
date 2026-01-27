# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CLI interface for the Ad Buyer System."""

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ...clients.opendirect_client import OpenDirectClient
from ...config.settings import settings
from ...flows.deal_booking_flow import DealBookingFlow
from ...models.flow_state import BookingState
from ...tools.research.product_search import ProductSearchTool

app = typer.Typer(
    name="ad-buyer",
    help="Advertising Buyer Agent CLI - Book deals using IAB OpenDirect standards",
    no_args_is_help=True,
)
console = Console()


def _create_client() -> OpenDirectClient:
    """Create OpenDirect client from settings."""
    return OpenDirectClient(
        base_url=settings.opendirect_base_url,
        oauth_token=settings.opendirect_token,
        api_key=settings.opendirect_api_key,
    )


@app.command()
def book(
    brief_file: Path = typer.Argument(
        ...,
        help="Path to campaign brief JSON file",
        exists=True,
        readable=True,
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview recommendations without booking",
    ),
    auto_approve: bool = typer.Option(
        False,
        "--auto-approve",
        "-y",
        help="Automatically approve all recommendations",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save results to JSON file",
    ),
) -> None:
    """Book advertising inventory based on campaign brief.

    Reads a campaign brief JSON file and runs the full booking workflow:
    1. Budget allocation across channels
    2. Inventory research by channel specialists
    3. Recommendation consolidation
    4. Human approval (unless --auto-approve)
    5. Booking execution
    """
    # Load brief
    try:
        with open(brief_file) as f:
            brief = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[red]Error parsing campaign brief:[/red] {e}")
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold blue]Campaign:[/bold blue] {brief.get('name', 'Unnamed')}\n"
            f"[bold]Budget:[/bold] ${brief.get('budget', 0):,.2f}\n"
            f"[bold]Flight:[/bold] {brief.get('start_date')} to {brief.get('end_date')}",
            title="Loading Campaign Brief",
        )
    )

    if dry_run:
        console.print("[yellow]DRY RUN MODE - No bookings will be made[/yellow]\n")

    # Initialize flow
    client = _create_client()
    flow = DealBookingFlow(client)
    flow.state = BookingState(campaign_brief=brief)

    # Run the flow
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Running booking workflow...", total=None)

        try:
            result = flow.kickoff()
            progress.update(task, description="Workflow complete")
        except Exception as e:
            console.print(f"[red]Error running workflow:[/red] {e}")
            raise typer.Exit(1)

    # Show budget allocation
    console.print("\n[bold]Budget Allocation:[/bold]")
    alloc_table = Table()
    alloc_table.add_column("Channel", style="cyan")
    alloc_table.add_column("Budget", justify="right", style="green")
    alloc_table.add_column("Percentage", justify="right")
    alloc_table.add_column("Rationale")

    for channel, alloc in flow.state.budget_allocations.items():
        alloc_table.add_row(
            channel.replace("_", " ").title(),
            f"${alloc.budget:,.2f}",
            f"{alloc.percentage:.1f}%",
            alloc.rationale[:50] + "..." if len(alloc.rationale) > 50 else alloc.rationale,
        )

    console.print(alloc_table)

    # Show recommendations
    if flow.state.pending_approvals:
        console.print("\n[bold]Recommendations:[/bold]")
        rec_table = Table()
        rec_table.add_column("#", style="dim")
        rec_table.add_column("Channel", style="cyan")
        rec_table.add_column("Product", style="magenta")
        rec_table.add_column("Publisher")
        rec_table.add_column("Impressions", justify="right")
        rec_table.add_column("CPM", justify="right")
        rec_table.add_column("Cost", justify="right", style="green")

        for i, rec in enumerate(flow.state.pending_approvals, 1):
            rec_table.add_row(
                str(i),
                rec.channel.replace("_", " ").title(),
                rec.product_name[:30],
                rec.publisher[:20],
                f"{rec.impressions:,}",
                f"${rec.cpm:.2f}",
                f"${rec.cost:,.2f}",
            )

        console.print(rec_table)

        # Calculate totals
        total_imps = sum(r.impressions for r in flow.state.pending_approvals)
        total_cost = sum(r.cost for r in flow.state.pending_approvals)
        console.print(
            f"\n[bold]Total:[/bold] {total_imps:,} impressions, ${total_cost:,.2f}"
        )

        # Handle approval
        if dry_run:
            console.print("\n[yellow]Dry run complete. No bookings made.[/yellow]")
        elif auto_approve:
            console.print("\n[green]Auto-approving all recommendations...[/green]")
            result = flow.approve_all()
            _show_booking_result(result)
        else:
            # Interactive approval
            approve = typer.confirm("\nApprove all recommendations?")
            if approve:
                result = flow.approve_all()
                _show_booking_result(result)
            else:
                console.print("[yellow]Booking cancelled.[/yellow]")

    else:
        console.print("\n[yellow]No recommendations generated.[/yellow]")

    # Show errors if any
    if flow.state.errors:
        console.print("\n[red]Errors:[/red]")
        for error in flow.state.errors:
            console.print(f"  - {error}")

    # Save output if requested
    if output:
        output_data = {
            "status": flow.get_status(),
            "recommendations": [r.model_dump() for r in flow.state.pending_approvals],
            "booked_lines": [b.model_dump() for b in flow.state.booked_lines],
        }
        with open(output, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        console.print(f"\n[green]Results saved to {output}[/green]")


def _show_booking_result(result: dict) -> None:
    """Display booking execution result."""
    if result.get("status") == "success":
        console.print(
            Panel(
                f"[green]Bookings Executed Successfully![/green]\n\n"
                f"Lines Booked: {result.get('booked', 0)}\n"
                f"Total Impressions: {result.get('total_impressions', 0):,}\n"
                f"Total Cost: ${result.get('total_cost', 0):,.2f}",
                title="Booking Complete",
            )
        )
    else:
        console.print(f"[red]Booking failed:[/red] {result.get('error', 'Unknown error')}")


@app.command()
def search(
    channel: Optional[str] = typer.Option(
        None,
        "--channel",
        "-c",
        help="Channel: display, video, mobile, ctv",
    ),
    format: Optional[str] = typer.Option(
        None,
        "--format",
        "-f",
        help="Ad format: banner, video, interstitial",
    ),
    min_price: Optional[float] = typer.Option(
        None,
        "--min-price",
        help="Minimum CPM price",
    ),
    max_price: Optional[float] = typer.Option(
        None,
        "--max-price",
        help="Maximum CPM price",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum results to return",
    ),
) -> None:
    """Search available advertising inventory.

    Query the OpenDirect API for available products matching the criteria.
    """
    client = _create_client()
    tool = ProductSearchTool(client)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(f"Searching {channel or 'all'} inventory...", total=None)

        result = tool._run(
            channel=channel,
            format=format,
            min_price=min_price,
            max_price=max_price,
            limit=limit,
        )

    console.print(result)


@app.command()
def status(
    order_id: str = typer.Argument(..., help="Order ID to check"),
    account_id: str = typer.Option(
        ...,
        "--account",
        "-a",
        help="Account ID",
    ),
) -> None:
    """Check status of an order and its lines."""
    client = _create_client()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching order status...", total=None)

        try:
            order = asyncio.run(client.get_order(account_id, order_id))
            lines = asyncio.run(client.list_lines(account_id, order_id))
        except Exception as e:
            console.print(f"[red]Error fetching order:[/red] {e}")
            raise typer.Exit(1)

    # Show order
    console.print(
        Panel(
            f"[bold]Order ID:[/bold] {order.id}\n"
            f"[bold]Name:[/bold] {order.name}\n"
            f"[bold]Status:[/bold] {order.order_status.value}\n"
            f"[bold]Budget:[/bold] {order.currency} {order.budget:,.2f}\n"
            f"[bold]Flight:[/bold] {order.start_date.date()} to {order.end_date.date()}",
            title="Order Details",
        )
    )

    # Show lines
    if lines:
        console.print("\n[bold]Line Items:[/bold]")
        table = Table()
        table.add_column("Line ID", style="cyan")
        table.add_column("Name")
        table.add_column("Status", style="magenta")
        table.add_column("Quantity", justify="right")
        table.add_column("Rate", justify="right")
        table.add_column("Cost", justify="right", style="green")

        for line in lines:
            cost = line.cost or (line.quantity / 1000 * line.rate)
            table.add_row(
                line.id or "N/A",
                line.name[:30],
                line.booking_status.value,
                f"{line.quantity:,}",
                f"${line.rate:.2f}",
                f"${cost:,.2f}",
            )

        console.print(table)
    else:
        console.print("\n[yellow]No line items found.[/yellow]")


@app.command()
def chat() -> None:
    """Start interactive chat session with the buyer agent.

    Launch a conversational interface to interact with the ad buying system
    using natural language.
    """
    from ..chat.main import ChatInterface

    console.print(
        Panel(
            "[bold blue]Ad Buyer Chat Interface[/bold blue]\n\n"
            "Type your requests in natural language.\n"
            "Examples:\n"
            "  - 'Search for CTV inventory under $20 CPM'\n"
            "  - 'What branding options are available?'\n"
            "  - 'Help me plan a campaign for $50,000'\n\n"
            "Type 'quit' or 'exit' to leave.",
            title="Welcome",
        )
    )

    chat_interface = ChatInterface()

    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ")
        except (KeyboardInterrupt, EOFError):
            break

        if user_input.lower() in ["quit", "exit", "q"]:
            break

        if not user_input.strip():
            continue

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Thinking...", total=None)
            response = chat_interface.process_message(user_input)

        console.print(f"\n[bold blue]Agent:[/bold blue] {response}")

    console.print("\n[dim]Goodbye![/dim]")


@app.command()
def init() -> None:
    """Initialize a new campaign brief template.

    Creates a sample campaign_brief.json file that you can edit.
    """
    template = {
        "name": "My Campaign",
        "objectives": ["brand awareness", "reach"],
        "budget": 50000,
        "start_date": "2025-02-01",
        "end_date": "2025-02-28",
        "target_audience": {
            "age": "25-54",
            "gender": "all",
            "geo": ["US"],
            "interests": ["technology", "business"],
        },
        "kpis": {
            "viewability": 70,
            "brand_safety": True,
        },
        "channels": ["branding", "ctv", "performance"],
    }

    output_path = Path("campaign_brief.json")
    if output_path.exists():
        overwrite = typer.confirm(f"{output_path} already exists. Overwrite?")
        if not overwrite:
            raise typer.Exit(0)

    with open(output_path, "w") as f:
        json.dump(template, f, indent=2)

    console.print(f"[green]Created {output_path}[/green]")
    console.print("\nEdit this file with your campaign details, then run:")
    console.print(f"  [cyan]ad-buyer book {output_path}[/cyan]")


if __name__ == "__main__":
    app()
