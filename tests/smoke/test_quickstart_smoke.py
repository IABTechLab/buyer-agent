# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quickstart smoke test — proves the *documented* startup command works.

The buyer quickstart (README.md → "Quick Start" / "Run" / "Verify") tells a
fresh-clone developer to run:

    python -m ad_buyer.interfaces.api.main      # serves on http://localhost:8000

(equivalently ``uvicorn ad_buyer.interfaces.api.main:app --port 8000``) and
then hit ``GET /health`` and the booking/product endpoints. This test is the
executable contract behind that promise. It:

1. imports the app at the documented module path
   (``ad_buyer.interfaces.api.main:app``) — if that path is wrong or the app
   fails to import, this fails at collection;
2. boots it through the real ASGI lifespan via ``TestClient`` as a context
   manager (order-router mount + MCP session manager), exactly as the
   documented ``python -m ...`` / uvicorn entrypoint would — no real network,
   no LLM calls;
3. exercises the documented health endpoint and one representative real
   endpoint (``GET /bookings``, the booking list) that needs no seller or
   LLM backend.

If someone renames the module, moves ``app``, or breaks startup, the docs'
entrypoint is now a lie and this test goes red.

Note on scope: the README's ``/products/search`` and cross-service
``/media-kit`` calls require a running seller/OpenDirect backend, so they are
deliberately NOT exercised here — this smoke test stays honest to what works
from a bare clone with no external services (see EP-9.2). ``anthropic_api_key``
defaults to empty in buyer settings, so no key is needed to boot the server;
it is only needed to actually run CrewAI booking flows.
"""

from fastapi.testclient import TestClient

from ad_buyer.interfaces.api.main import app


def test_documented_entrypoint_boots_and_serves():
    """Boot the app through its real lifespan and hit the documented endpoints."""
    # Context-manager form runs startup + shutdown (lifespan), i.e. the same
    # path `python -m ad_buyer.interfaces.api.main` / uvicorn takes.
    with TestClient(app) as client:
        # 1) Health — the quickstart's "Verify" step.
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy", "version": "1.0.0"}

        # 2) Representative real endpoint that needs no external backend:
        #    the booking list. Proves the bookings surface is mounted and
        #    served by the documented entrypoint. Fresh server → no jobs yet.
        resp = client.get("/bookings")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload == {"jobs": [], "total": 0}
