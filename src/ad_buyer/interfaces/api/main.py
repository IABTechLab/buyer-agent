# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FastAPI server for the Ad Buyer System."""

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import sys

from ...clients.opendirect_client import OpenDirectClient
from ...config.settings import settings
from ...flows.deal_booking_flow import DealBookingFlow
from ...models.flow_state import BookingState
from ...storage import DealStore
from ...storage.order_store import OrderStore

logger = logging.getLogger(__name__)


def _current_settings():
    """Get the current settings from this module's namespace.

    Uses sys.modules lookup so that test patches to the module-level
    ``settings`` attribute are visible to the middleware at request time.
    """
    return sys.modules[__name__].settings

app = FastAPI(
    title="Ad Buyer Agent API",
    description=(
        "Automated advertising buyer agent using CrewAI and IAB OpenDirect 2.1. "
        "Orchestrates budget allocation, inventory research, recommendation "
        "consolidation, and deal booking against seller agent APIs."
    ),
    version="1.0.0",
    contact={"name": "IAB Tech Lab", "url": "https://iabtechlab.com"},
    license_info={"name": "Apache 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness"},
        {"name": "Bookings", "description": "Campaign booking workflow lifecycle"},
        {"name": "Products", "description": "Seller inventory product search"},
        {"name": "Events", "description": "Event bus query endpoints"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount MCP SSE server
from ..mcp_server import mount_mcp
mount_mcp(app)

# Mount order status/audit router (buyer-nz9)
from .order_endpoints import create_order_router

# Lazy OrderStore singleton
_order_store: Optional[OrderStore] = None


def _get_order_store() -> Optional[OrderStore]:
    """Return a lazily-initialised OrderStore singleton.

    Returns None (and logs a warning) if initialisation fails so that
    the API can continue operating without order persistence.
    """
    global _order_store
    if _order_store is not None:
        return _order_store
    try:
        current = _current_settings()
        _order_store = OrderStore(current.database_url)
        _order_store.connect()
        return _order_store
    except (sqlite3.Error, OSError, ValueError, AttributeError):
        logger.exception("Failed to initialise OrderStore; order endpoints unavailable")
        return None


def _mount_order_router() -> None:
    """Mount the order router if OrderStore initialises successfully."""
    store = _get_order_store()
    if store is not None:
        router = create_order_router(store)
        app.include_router(router)


_mount_order_router()

# Paths that never require authentication
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    """Validate X-API-Key header on all non-public endpoints.

    Authentication is skipped entirely when settings.api_key is empty,
    allowing easy local development without configuring a key.
    """
    current = _current_settings()

    # Skip auth if no api_key is configured (dev mode)
    if not current.api_key:
        return await call_next(request)

    # Skip auth for public/health endpoints
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    # Validate the API key
    provided_key = request.headers.get("X-API-Key", "")
    if not provided_key or provided_key != current.api_key:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
        )

    return await call_next(request)


# In-memory job storage (use Redis/DB in production)
jobs: dict[str, dict[str, Any]] = {}

# Lazy DealStore singleton
_deal_store: Optional[DealStore] = None


def _get_store() -> Optional[DealStore]:
    """Return a lazily-initialised DealStore singleton.

    Returns None (and logs a warning) if initialisation fails so that
    the API can continue operating with in-memory state only.
    """
    global _deal_store
    if _deal_store is not None:
        return _deal_store
    try:
        current = _current_settings()
        _deal_store = DealStore(current.database_url)
        _deal_store.connect()
        return _deal_store
    except (sqlite3.Error, OSError, ValueError, AttributeError):
        logger.exception("Failed to initialise DealStore; running without persistence")
        return None


def _persist_job(job_id: str, job: dict[str, Any]) -> None:
    """Best-effort dual-write of a job dict to the DealStore.

    Never raises -- logs errors and continues so the API endpoint is
    unaffected by persistence failures.

    Args:
        job_id: Unique job identifier.
        job: The in-memory job dict.
    """
    store = _get_store()
    if store is None:
        return
    try:
        store.save_job(
            job_id=job_id,
            status=job.get("status", "pending"),
            progress=job.get("progress", 0.0),
            brief=json.dumps(job.get("brief", {})),
            auto_approve=job.get("auto_approve", False),
            budget_allocs=json.dumps(job.get("budget_allocations", {})),
            recommendations=json.dumps(job.get("recommendations", [])),
            booked_lines=json.dumps(job.get("booked_lines", [])),
            errors=json.dumps(job.get("errors", [])),
        )
    except (sqlite3.Error, OSError, ValueError, AttributeError):
        logger.exception("Failed to persist job %s", job_id)


# Request/Response Models
class CampaignBrief(BaseModel):
    """Campaign brief for booking."""

    name: str = Field(..., min_length=1, max_length=100)
    objectives: list[str] = Field(..., min_length=1)
    budget: float = Field(..., gt=0)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    target_audience: dict[str, Any]
    kpis: dict[str, Any] = Field(default_factory=dict)
    channels: Optional[list[str]] = None


class BookingRequest(BaseModel):
    """Request to start a booking workflow."""

    brief: CampaignBrief
    auto_approve: bool = Field(
        default=False,
        description="Automatically approve all recommendations",
    )


class BookingResponse(BaseModel):
    """Response from booking creation."""

    job_id: str
    status: str
    message: str


class BookingStatus(BaseModel):
    """Status of a booking job."""

    job_id: str
    status: str
    progress: float
    budget_allocations: Optional[dict[str, Any]] = None
    recommendations: Optional[list[dict[str, Any]]] = None
    booked_lines: Optional[list[dict[str, Any]]] = None
    errors: Optional[list[str]] = None
    created_at: str
    updated_at: str


class ApprovalRequest(BaseModel):
    """Request to approve recommendations."""

    approved_product_ids: list[str]


class ProductSearchRequest(BaseModel):
    """Request to search products."""

    channel: Optional[str] = None
    format: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    limit: int = Field(default=10, ge=1, le=50)


def _create_client() -> OpenDirectClient:
    """Create OpenDirect client from settings."""
    return OpenDirectClient(
        base_url=settings.opendirect_base_url,
        oauth_token=settings.opendirect_token,
        api_key=settings.opendirect_api_key,
    )


@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


@app.post("/bookings", response_model=BookingResponse, tags=["Bookings"])
async def create_booking(
    request: BookingRequest,
    background_tasks: BackgroundTasks,
) -> BookingResponse:
    """Start a new booking workflow.

    Creates a background job that runs the full booking flow:
    1. Budget allocation
    2. Inventory research
    3. Recommendation consolidation
    4. (Optional) Automatic approval

    Use GET /bookings/{job_id} to check status.
    """
    job_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    jobs[job_id] = {
        "status": "pending",
        "progress": 0.0,
        "brief": request.brief.model_dump(),
        "auto_approve": request.auto_approve,
        "budget_allocations": {},
        "recommendations": [],
        "booked_lines": [],
        "errors": [],
        "created_at": now,
        "updated_at": now,
    }

    # Dual-write to SQLite
    _persist_job(job_id, jobs[job_id])

    # Run booking flow in background
    background_tasks.add_task(_run_booking_flow, job_id, request)

    return BookingResponse(
        job_id=job_id,
        status="pending",
        message="Booking workflow started. Use GET /bookings/{job_id} to check status.",
    )


@app.get("/bookings/{job_id}", response_model=BookingStatus, tags=["Bookings"])
async def get_booking_status(job_id: str) -> BookingStatus:
    """Get status of a booking workflow."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return BookingStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        budget_allocations=job.get("budget_allocations"),
        recommendations=job.get("recommendations"),
        booked_lines=job.get("booked_lines"),
        errors=job.get("errors"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


@app.post("/bookings/{job_id}/approve", tags=["Bookings"])
async def approve_recommendations(
    job_id: str,
    request: ApprovalRequest,
) -> dict[str, Any]:
    """Approve specific recommendations for booking.

    Call this endpoint after the job reaches 'awaiting_approval' status.
    Pass the product IDs you want to approve for booking.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting approval. Current status: {job['status']}",
        )

    # Get the flow from the job (in production, restore from storage)
    flow = job.get("_flow")
    if not flow:
        raise HTTPException(
            status_code=500,
            detail="Flow state not available. Job may have expired.",
        )

    # Execute approvals
    result = flow.approve_recommendations(request.approved_product_ids)

    # Update job
    job["status"] = "completed" if result.get("status") == "success" else "failed"
    job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
    job["updated_at"] = datetime.utcnow().isoformat()
    job["progress"] = 1.0

    # Dual-write to SQLite
    _persist_job(job_id, job)

    return {
        "status": result.get("status"),
        "approved_count": len(request.approved_product_ids),
        "booked": result.get("booked", 0),
        "total_cost": result.get("total_cost", 0),
    }


@app.post("/bookings/{job_id}/approve-all", tags=["Bookings"])
async def approve_all_recommendations(job_id: str) -> dict[str, Any]:
    """Approve all recommendations for booking."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting approval. Current status: {job['status']}",
        )

    flow = job.get("_flow")
    if not flow:
        raise HTTPException(
            status_code=500,
            detail="Flow state not available. Job may have expired.",
        )

    result = flow.approve_all()

    job["status"] = "completed" if result.get("status") == "success" else "failed"
    job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
    job["updated_at"] = datetime.utcnow().isoformat()
    job["progress"] = 1.0

    # Dual-write to SQLite
    _persist_job(job_id, job)

    return result


@app.get("/bookings", tags=["Bookings"])
async def list_bookings(
    status: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List all booking jobs."""
    job_list = []
    for job_id, job in jobs.items():
        if status and job["status"] != status:
            continue
        job_list.append({
            "job_id": job_id,
            "status": job["status"],
            "campaign_name": job["brief"].get("name"),
            "budget": job["brief"].get("budget"),
            "created_at": job["created_at"],
        })

    # Sort by created_at descending
    job_list.sort(key=lambda x: x["created_at"], reverse=True)

    return {"jobs": job_list[:limit], "total": len(job_list)}


@app.post("/products/search", tags=["Products"])
async def search_products(request: ProductSearchRequest) -> dict[str, Any]:
    """Search available advertising products."""
    from ...tools.research.product_search import ProductSearchTool

    client = _create_client()
    tool = ProductSearchTool(client)

    result = tool._run(
        channel=request.channel,
        format=request.format,
        min_price=request.min_price,
        max_price=request.max_price,
        limit=request.limit,
    )

    return {"results": result}


# ---------------------------------------------------------------------------
# Event endpoints
# ---------------------------------------------------------------------------


@app.get("/events", tags=["Events"])
async def list_events(
    event_type: Optional[str] = None,
    flow_id: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List events from the event bus.

    Queries the in-memory event bus for recent events, with optional
    filtering by event_type, flow_id, or session_id.
    """
    from ...events.bus import get_event_bus

    bus = await get_event_bus()
    events = await bus.list_events(
        flow_id=flow_id,
        event_type=event_type,
        session_id=session_id,
        limit=limit,
    )
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "total": len(events),
    }


@app.get("/events/{event_id}", tags=["Events"])
async def get_event(event_id: str) -> dict[str, Any]:
    """Retrieve a single event by ID."""
    from ...events.bus import get_event_bus

    bus = await get_event_bus()
    event = await bus.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.model_dump(mode="json")


async def _run_booking_flow(job_id: str, request: BookingRequest) -> None:
    """Background task to run the booking flow."""
    job = jobs[job_id]

    try:
        job["status"] = "running"
        job["progress"] = 0.1
        job["updated_at"] = datetime.utcnow().isoformat()
        _persist_job(job_id, job)

        client = _create_client()
        flow = DealBookingFlow(client, store=_get_store())
        flow.state = BookingState(campaign_brief=request.brief.model_dump())

        # Store flow reference for approval
        job["_flow"] = flow

        job["progress"] = 0.2
        result = flow.kickoff()

        job["progress"] = 0.8
        job["budget_allocations"] = {
            k: v.model_dump() for k, v in flow.state.budget_allocations.items()
        }
        job["recommendations"] = [
            r.model_dump() for r in flow.state.pending_approvals
        ]

        if request.auto_approve:
            flow.approve_all()
            job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
            job["status"] = "completed"
        else:
            job["status"] = "awaiting_approval"

        job["progress"] = 1.0 if job["status"] == "completed" else 0.9
        job["updated_at"] = datetime.utcnow().isoformat()
        _persist_job(job_id, job)

    except Exception as e:  # noqa: BLE001 - top-level background task handler; must record any failure
        job["status"] = "failed"
        job["errors"].append(str(e))
        job["updated_at"] = datetime.utcnow().isoformat()
        _persist_job(job_id, job)


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
