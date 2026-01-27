# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FastAPI server for the Ad Buyer System."""

import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ...clients.opendirect_client import OpenDirectClient
from ...config.settings import settings
from ...flows.deal_booking_flow import DealBookingFlow
from ...models.flow_state import BookingState

app = FastAPI(
    title="Ad Buyer Agent API",
    description="API for automated advertising buying using CrewAI agents and IAB OpenDirect",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job storage (use Redis/DB in production)
jobs: dict[str, dict[str, Any]] = {}


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


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "version": "1.0.0"}


@app.post("/bookings", response_model=BookingResponse)
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

    # Run booking flow in background
    background_tasks.add_task(_run_booking_flow, job_id, request)

    return BookingResponse(
        job_id=job_id,
        status="pending",
        message="Booking workflow started. Use GET /bookings/{job_id} to check status.",
    )


@app.get("/bookings/{job_id}", response_model=BookingStatus)
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


@app.post("/bookings/{job_id}/approve")
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

    return {
        "status": result.get("status"),
        "approved_count": len(request.approved_product_ids),
        "booked": result.get("booked", 0),
        "total_cost": result.get("total_cost", 0),
    }


@app.post("/bookings/{job_id}/approve-all")
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

    return result


@app.get("/bookings")
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


@app.post("/products/search")
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


async def _run_booking_flow(job_id: str, request: BookingRequest) -> None:
    """Background task to run the booking flow."""
    job = jobs[job_id]

    try:
        job["status"] = "running"
        job["progress"] = 0.1
        job["updated_at"] = datetime.utcnow().isoformat()

        client = _create_client()
        flow = DealBookingFlow(client)
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

    except Exception as e:
        job["status"] = "failed"
        job["errors"].append(str(e))
        job["updated_at"] = datetime.utcnow().isoformat()


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the API server."""
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
