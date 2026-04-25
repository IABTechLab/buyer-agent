"""AgentCore HTTP entrypoint for the IAB AAMP Buyer Agent.

Location: src/ad_buyer/interfaces/agentcore/http_main.py

Uses the BedrockAgentCoreApp wrapper required by Amazon Bedrock AgentCore.
Deploy via the ``agentcore`` CLI — see ``infra/aws/agentcore/deploy.sh``.

Architecture:
    The buyer agent handles ONE thing well: campaign planning via
    DealBookingFlow. All seller interactions (inventory, pricing, deals)
    are handled by the seller runtime separately.

    Prompt → _handle_invocation → _run_campaign_plan_crew → DealBookingFlow
                                                                ↓
                                                          PortfolioCrew (Bedrock LLM)
                                                                ↓
                                                          Channel specialists
                                                                ↓
                                                          Budget allocations

Routing modes (``ROUTING_MODE`` env var or ``routing_mode`` payload field):
- ``crew`` (default): Runs DealBookingFlow with Bedrock LLM for campaign
  planning and budget allocation.
- ``chat``: Routes through the buyer's ChatInterface for keyword-based
  responses. Fallback for non-planning queries.

Local testing::

    pip install bedrock-agentcore
    python src/ad_buyer/interfaces/agentcore/http_main.py
    # In another terminal:
    curl -X POST http://localhost:8080/invocations \\
      -H "Content-Type: application/json" \\
      -d '{"prompt": "Plan a $500K Q4 automotive campaign across CTV and digital video"}'
"""

import asyncio
import json
import logging
import os
import re
import sys

# Add the src directory to Python path so ad_buyer is importable
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
if os.path.isdir(_src_dir):
    sys.path.insert(0, _src_dir)

# Environment defaults for AgentCore / workshop demo mode
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-with-bedrock")
os.environ.setdefault("STORAGE_TYPE", "sqlite")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from bedrock_agentcore.runtime import BedrockAgentCoreApp

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Internal port for FastAPI background server (for DealBookingFlow)
_INTERNAL_PORT = int(os.environ.get("INTERNAL_API_PORT", "8001"))

# Track whether the background FastAPI server has been started
_fastapi_started = False


def _start_fastapi_background():
    """Start FastAPI on internal port in a background thread.

    Required for DealBookingFlow which uses the buyer's REST API internally.
    Uses uvicorn.Server with a dedicated asyncio event loop in a daemon thread.
    Health check loop: 30 attempts × 0.5s = 15s timeout.

    Idempotent — safe to call multiple times; only starts once.
    """
    global _fastapi_started

    if _fastapi_started:
        return

    import threading
    import time

    import uvicorn

    from ad_buyer.interfaces.api.main import app as fastapi_app

    os.environ["BUYER_API_URL"] = f"http://localhost:{_INTERNAL_PORT}"

    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=_INTERNAL_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    thread = threading.Thread(target=_run, daemon=True, name="fastapi-bg")
    thread.start()

    for _ in range(30):
        try:
            import httpx

            resp = httpx.get(
                f"http://localhost:{_INTERNAL_PORT}/health", timeout=1.0
            )
            if resp.status_code == 200:
                logger.info(
                    "FastAPI background server ready on port %d",
                    _INTERNAL_PORT,
                )
                _fastapi_started = True
                return
        except Exception:
            time.sleep(0.5)

    logger.error(
        "FastAPI failed to start on port %d within 15s", _INTERNAL_PORT
    )
    raise RuntimeError(f"FastAPI background server failed to start on port {_INTERNAL_PORT}")


# ---------------------------------------------------------------------------
# Lazy-initialized ChatInterface (fallback for non-planning queries)
# ---------------------------------------------------------------------------
_chat = None


def _get_chat():
    """Get or create the buyer ChatInterface."""
    global _chat
    if _chat is None:
        try:
            from ad_buyer.interfaces.chat.main import ChatInterface
            _chat = ChatInterface()
            logger.info("Buyer ChatInterface ready")
        except Exception as exc:
            logger.warning("ChatInterface init failed (non-fatal): %s", exc)
            _chat = None
    return _chat


# ---------------------------------------------------------------------------
# Routing mode
# ---------------------------------------------------------------------------
_VALID_ROUTING_MODES = {"chat", "crew"}
_DEFAULT_ROUTING_MODE = os.environ.get("ROUTING_MODE", "crew")


def _get_routing_mode(payload: dict) -> str:
    """Determine routing mode from payload field or ROUTING_MODE env var.

    Priority: payload["routing_mode"] > ROUTING_MODE env var > default ("crew").
    Invalid values fall back to default for backward compatibility.
    """
    mode = (
        payload.get("routing_mode")
        or os.environ.get("ROUTING_MODE", _DEFAULT_ROUTING_MODE)
    )
    mode = str(mode).strip().lower()
    if mode not in _VALID_ROUTING_MODES:
        logger.warning("Invalid routing mode %r, falling back to %r", mode, _DEFAULT_ROUTING_MODE)
        return _DEFAULT_ROUTING_MODE
    return mode


# ---------------------------------------------------------------------------
# Structured output formatting
# ---------------------------------------------------------------------------

_DEAL_ID_PATTERN = re.compile(r"DEAL-[\w-]+", re.IGNORECASE)
_CPM_PATTERN = re.compile(r"\$?([\d]+(?:\.[\d]{1,2})?)\s*(?:CPM|cpm)", re.IGNORECASE)
_BUDGET_PATTERN = re.compile(r"\$?([\d,]+(?:\.[\d]{1,2})?)\s*(?:budget|total)", re.IGNORECASE)


def _format_crew_output(crew_output) -> dict:
    """Parse CrewOutput into a JSON-serializable dict with visualization tags."""
    raw_text = getattr(crew_output, "raw", "") or ""

    structured_data = None
    if getattr(crew_output, "json_dict", None):
        structured_data = crew_output.json_dict
    elif getattr(crew_output, "pydantic", None):
        try:
            structured_data = crew_output.pydantic.model_dump()
        except Exception:
            pass

    deal_ids = _DEAL_ID_PATTERN.findall(raw_text)
    cpm_values = _CPM_PATTERN.findall(raw_text)
    budget_values = _BUDGET_PATTERN.findall(raw_text)

    metadata = {
        "type": "buyer_response",
        "routing_mode": "crew",
    }

    viz_data = {}
    if deal_ids:
        viz_data["deal_ids"] = deal_ids
        metadata["deal_ids"] = deal_ids
    if cpm_values:
        viz_data["cpm_values"] = [float(v) for v in cpm_values]
    if budget_values:
        viz_data["budget_values"] = [v.replace(",", "") for v in budget_values]
    if structured_data:
        viz_data["structured_output"] = structured_data

    response_text = raw_text
    if viz_data:
        viz_json = json.dumps(viz_data, default=str)
        response_text = (
            f"{raw_text}\n\n"
            f"<visualization-data>{viz_json}</visualization-data>"
        )

    return {
        "response": response_text,
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Crew routing path — DealBookingFlow campaign planning
# ---------------------------------------------------------------------------

async def _handle_crew_invocation(payload: dict) -> dict:
    """Handle a crew-mode invocation via DealBookingFlow.

    The buyer agent has one job: campaign planning with budget allocation.
    The PortfolioCrew inside DealBookingFlow has its own LLM that extracts
    campaign parameters from the natural language prompt and allocates budget.
    We pass the prompt straight through — no pre-extraction needed.

    All seller interactions (inventory, pricing, deals) are handled by the
    seller runtime separately.
    """
    prompt = (
        payload.get("prompt")
        or payload.get("message")
        or payload.get("input", "")
    )
    if not prompt:
        return {"error": "Missing 'prompt', 'message', or 'input' field"}

    logger.info("Crew invocation — prompt: %s", prompt[:80])

    try:
        import concurrent.futures
        from .crew_tools import run_campaign_plan

        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = await loop.run_in_executor(
                pool, run_campaign_plan, prompt, None
            )

        # Return structured plan data as JSON
        response_text = json.dumps(result, indent=2, default=str)

        return {
            "response": response_text,
            "metadata": {
                "type": "buyer_campaign_plan",
                "routing_mode": "crew",
                "approval_required": result.get("approval_required", True),
            },
        }

    except Exception as exc:
        logger.exception("Crew invocation failed: %s", exc)
        return {"error": "Crew invocation failed", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Chat routing path (fallback)
# ---------------------------------------------------------------------------

def _handle_chat_invocation(payload: dict) -> dict:
    """Handle a chat-mode invocation via ChatInterface."""
    prompt = (
        payload.get("prompt")
        or payload.get("message")
        or payload.get("input", "")
    )
    if not prompt:
        return {"error": "Missing 'prompt', 'message', or 'input' field"}

    chat = _get_chat()
    if chat is not None:
        try:
            result = chat.process_message(prompt)
            return {
                "response": result,
                "metadata": {"type": "portfolio_manager_response"},
            }
        except Exception as exc:
            logger.warning("ChatInterface.process_message failed: %s", exc)

    return {
        "response": (
            "I'm the AAMP Buyer Agent. I help plan advertising campaigns with "
            "budget allocation across channels (CTV, digital video, display, mobile, audio). "
            "Tell me your budget, timeline, target audience, and preferred channels, "
            "and I'll create a media plan.\n\n"
            "Example: *Plan a $500K Q4 automotive campaign across CTV and digital video "
            "targeting adults 25-54.*"
        ),
        "metadata": {"type": "buyer_fallback_response"},
    }


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def _handle_invocation(payload: dict):
    """Async handler — routes to crew (DealBookingFlow) or chat based on mode.

    UI payloads (agent_name/memory_id present, no routing_mode) default to
    crew mode for campaign planning.
    """
    routing_mode = _get_routing_mode(payload)

    # UI sends payloads with agent_name/memory_id but no routing_mode.
    # Default to crew for UI calls.
    if routing_mode == "chat" and not payload.get("routing_mode"):
        if payload.get("agent_name") or payload.get("memory_id") or payload.get("direct_mention_target"):
            routing_mode = "crew"
            logger.info("Auto-routing to crew mode (UI payload detected)")

    if routing_mode == "crew":
        return await _handle_crew_invocation(payload)

    return _handle_chat_invocation(payload)


@app.entrypoint
def invoke(payload, context):
    """Handle an AgentCore invocation.

    Bridges the sync ``@app.entrypoint`` to the async buyer code via
    ``asyncio.run()``.
    """
    try:
        return asyncio.run(_handle_invocation(payload))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _handle_invocation(payload))
            return future.result(timeout=120)
    except Exception as exc:
        logger.exception("Invocation failed: %s", exc)
        return {"error": "Invocation failed", "detail": str(exc)}


if __name__ == "__main__":
    _start_fastapi_background()
    app.run()
