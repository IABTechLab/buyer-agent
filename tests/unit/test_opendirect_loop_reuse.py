# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression test: OpenDirectClient must survive per-call event loops.

The sync CrewAI tools (e.g. ``ProductSearchTool``) drive the async client
through ``ad_buyer.async_utils.run_async``, which runs EACH call on a fresh
event loop that is closed as soon as the call returns. A persistent
``httpx.AsyncClient`` binds its connection pool to the first loop, so the
second and every later call fails with ``RuntimeError: Event loop is closed``
(seen live as ~19 consecutive product-search failures -> nothing booked).

The reproduction needs a real socket transport: it is the pooled keep-alive
connection (bound to the first, now-closed loop) that blows up on reuse, so
the test runs a stdlib HTTP/1.1 server on 127.0.0.1 (loopback only, no
external network). The key property: TWO sequential ``run_async`` calls both
succeed.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from ad_buyer.async_utils import run_async
from ad_buyer.clients.opendirect_client import OpenDirectClient

# Shared ProductListResponse envelope with one valid wire Product.
PRODUCT_LIST_PAYLOAD = {
    "products": [
        {
            "product_id": "prod_1",
            "seller_organization_id": "pub_1",
            "name": "Homepage Banner",
            "base_price": {"amount_micros": 15_000_000, "currency": "USD"},
            "pricing_model": "cpm",
            "delivery_type": "Guaranteed",
            "ad_formats": ["banner"],
        }
    ],
    "total_count": 1,
    "limit": 50,
    "offset": 0,
}


class _CatalogHandler(BaseHTTPRequestHandler):
    """Serves the product catalog over keep-alive HTTP/1.1."""

    protocol_version = "HTTP/1.1"  # keep-alive, so the connection is pooled

    def do_GET(self):
        body = json.dumps(PRODUCT_LIST_PAYLOAD).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def catalog_server() -> Iterator[str]:
    """Loopback HTTP server; yields its base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CatalogHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestOpenDirectClientLoopReuse:
    """The client must not bind HTTP state to any single event loop."""

    def test_two_sequential_run_async_calls_both_succeed(self, catalog_server):
        """Mirrors the CrewAI sync-tool call pattern: each run_async call runs
        on a fresh event loop that is closed afterwards. Before the
        per-request-client fix, call 2 raised RuntimeError('Event loop is
        closed') because the persistent AsyncClient's pooled connection was
        bound to the closed first loop.
        """
        client = OpenDirectClient(base_url=catalog_server, api_key="test_key")

        first = run_async(client.list_products(skip=0, top=50))
        second = run_async(client.list_products(skip=0, top=50))

        assert [p.id for p in first] == ["prod_1"]
        assert [p.id for p in second] == ["prod_1"]

    def test_search_products_twice_via_run_async(self, catalog_server):
        """search_products is the method the ProductSearchTool actually drives
        (~19x per crew run in the live failure)."""
        client = OpenDirectClient(base_url=catalog_server)

        first = run_async(client.search_products({"adFormat": "banner"}))
        second = run_async(client.search_products({"adFormat": "banner"}))

        assert [p.id for p in first] == ["prod_1"]
        assert [p.id for p in second] == ["prod_1"]

    def test_mock_transport_injection_seam(self):
        """The per-request client honors an injected transport (no sockets),
        and repeated run_async calls succeed through the full wire-parsing
        path (ProductListResponse -> from_wire_product)."""
        client = OpenDirectClient(base_url="http://test.local", api_key="test_key")
        seen_headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.append(request.headers)
            return httpx.Response(200, json=PRODUCT_LIST_PAYLOAD)

        client._transport = httpx.MockTransport(handler)

        first = run_async(client.list_products(skip=0, top=50))
        second = run_async(client.list_products(skip=0, top=50))

        assert [p.id for p in first] == ["prod_1"]
        assert [p.id for p in second] == ["prod_1"]
        # Configured headers are applied on every per-request client.
        assert all(h["X-API-Key"] == "test_key" for h in seen_headers)
