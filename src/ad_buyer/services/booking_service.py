# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Campaign booking application service.

Owns the booking workflow logic that used to live inline in the HTTP
interface (``interfaces/api/main.py``): job-record construction, the
canonical ``DealBookingFlow`` execution (with the hold-for-approval
handoff), the approval execution, and persistence of job state to the
``DealStore`` jobs table (the durable source of truth).

State-management note: a live CrewAI ``Flow`` cannot be serialised and
rehydrated, so the approval handoff still requires the caller to hold the
live flow object in an in-memory registry for the lifetime of the job.
This service therefore operates on the caller's mutable ``job`` dict and
persists a durable snapshot after every transition; the interface keeps
only the thin in-memory registry + HTTP wiring.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any, Callable

from ..flows.deal_booking_flow import DealBookingFlow
from ..time_utils import utc_now

logger = logging.getLogger(__name__)

# Callback signature used to snapshot a job after each transition.
PersistCallback = Callable[[str, dict[str, Any]], None]


def new_job_record(brief: dict[str, Any], auto_approve: bool) -> dict[str, Any]:
    """Build a fresh in-memory job record for a booking request."""
    now = utc_now().isoformat()
    return {
        "status": "pending",
        "progress": 0.0,
        "brief": brief,
        "auto_approve": auto_approve,
        "budget_allocations": {},
        "recommendations": [],
        "booked_lines": [],
        "errors": [],
        "created_at": now,
        "updated_at": now,
    }


def persist_job(store: Any, job_id: str, job: dict[str, Any]) -> None:
    """Best-effort snapshot of a job dict to the ``DealStore`` jobs table.

    Never raises -- logs and continues so the HTTP endpoint is unaffected
    by persistence failures.  A ``None`` store is a no-op.
    """
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


async def execute_booking(
    job_id: str,
    job: dict[str, Any],
    brief: dict[str, Any],
    auto_approve: bool,
    *,
    client: Any,
    store: Any,
    persist: PersistCallback,
) -> None:
    """Run the canonical DealBookingFlow, mutating ``job`` as it progresses.

    Mirrors the former ``api.main._run_booking_flow`` body: budget
    allocation + inventory research run inside the flow, results are
    surfaced onto ``job``, and the flow either auto-approves (booking
    executes via the orchestrator handoff) or holds for approval.

    The synchronous CrewAI ``flow.kickoff()`` / ``flow.approve_all()``
    calls are offloaded to worker threads via ``asyncio.to_thread`` so the
    caller's event loop stays responsive (buyer-1g4).  The live flow is
    stashed on ``job['_flow']`` so the approval endpoints can resume it.
    """
    try:
        job["status"] = "running"
        job["progress"] = 0.1
        job["updated_at"] = utc_now().isoformat()
        persist(job_id, job)

        # Pass initial state via constructor -- CrewAI removed the
        # flow.state setter, so campaign_brief is supplied here.
        flow = DealBookingFlow(client, store=store, campaign_brief=brief)

        # Store flow reference for the approval handoff.
        job["_flow"] = flow

        job["progress"] = 0.2
        # buyer-1g4: offload the sync flow.kickoff() to a worker thread so
        # the event loop stays free while the crew agents block on LLM I/O.
        _result = await asyncio.to_thread(flow.kickoff)

        # Propagate any errors captured by flow steps into the job response
        # so the client sees failures instead of silent-success (ar-jbod).
        if flow.state.errors:
            job["errors"].extend(flow.state.errors)

        job["progress"] = 0.8
        job["budget_allocations"] = {
            k: v.model_dump() for k, v in flow.state.budget_allocations.items()
        }
        job["recommendations"] = [r.model_dump() for r in flow.state.pending_approvals]

        if auto_approve:
            # buyer-1g4: same reason as kickoff -- offload sync work.
            await asyncio.to_thread(flow.approve_all)
            job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
            job["status"] = "completed"
        else:
            job["status"] = "awaiting_approval"

        job["progress"] = 1.0 if job["status"] == "completed" else 0.9
        job["updated_at"] = utc_now().isoformat()
        persist(job_id, job)

    except Exception as e:  # noqa: BLE001 - top-level task handler; record any failure
        job["status"] = "failed"
        job["errors"].append(str(e))
        job["updated_at"] = utc_now().isoformat()
        persist(job_id, job)


async def approve(
    job_id: str,
    job: dict[str, Any],
    approved_product_ids: list[str],
    *,
    store: Any,
    persist: PersistCallback,
) -> dict[str, Any]:
    """Execute approval for specific recommendations on a held job."""
    flow = job.get("_flow")
    result = flow.approve_recommendations(approved_product_ids)

    job["status"] = "completed" if result.get("status") == "success" else "failed"
    job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
    job["updated_at"] = utc_now().isoformat()
    job["progress"] = 1.0
    persist(job_id, job)

    return {
        "status": result.get("status"),
        "approved_count": len(approved_product_ids),
        "booked": result.get("booked", 0),
        "total_cost": result.get("total_cost", 0),
    }


async def approve_all(
    job_id: str,
    job: dict[str, Any],
    *,
    store: Any,
    persist: PersistCallback,
) -> dict[str, Any]:
    """Execute approval for all recommendations on a held job."""
    flow = job.get("_flow")

    # buyer-1g4: approve_all() runs sync CrewAI work; offload it.
    result = await asyncio.to_thread(flow.approve_all)

    job["status"] = "completed" if result.get("status") == "success" else "failed"
    job["booked_lines"] = [b.model_dump() for b in flow.state.booked_lines]
    job["updated_at"] = utc_now().isoformat()
    job["progress"] = 1.0
    persist(job_id, job)

    return result
