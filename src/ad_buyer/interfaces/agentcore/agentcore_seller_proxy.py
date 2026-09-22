# Author: contributed to IAB Tech Lab (AgentCore adapter)

"""AgentCore seller proxy — reach a deployed seller runtime by ARN.

Amazon Bedrock AgentCore exposes a deployed agent as an **ARN**, invoked via
the SigV4-signed ``InvokeAgentRuntime`` data-plane API — NOT as a plain HTTP
URL. The buyer's ``DealBookingFlow`` planning path, however, is written against
the ``OpenDirectClient`` surface (``list_products`` / ``search_products`` /
``get_product`` / ``check_avails``) and expects structured ``Product`` /
``AvailsResponse`` objects.

``AgentCoreSellerProxy`` bridges the two: it presents the same OpenDirect
method surface the flow needs, but under the hood calls the seller's **MCP**
runtime by ARN via ``bedrock-agentcore:InvokeAgentRuntime``, issuing MCP
``tools/call`` requests (``list_products``, ``get_product_details``,
``discover_inventory``) and mapping the STRUCTURED JSON tool results to the
OpenDirect models at the boundary.

Why MCP and not A2A for this path: the seller's MCP runtime returns structured
JSON tool results (deterministically parseable into ``Product`` models),
whereas the A2A runtime returns natural-language crew output. A2A is the right
surface for agent-to-agent *negotiation dialogue* (a booking-path concern); the
planning path needs structured product/avails data, so it targets MCP.

This replaces the previous ``localhost:9999`` stub in ``crew_tools`` for the
deployed (ARN) case. When ``SELLER_AGENT_URL`` is a plain HTTP URL (local dev),
the ordinary ``OpenDirectClient`` is used instead — this proxy is only for the
``arn:`` case.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...clients.contract_mappers import from_wire_product
from ...models.opendirect import AvailsRequest, AvailsResponse, Product

logger = logging.getLogger(__name__)

# Seller MCP tool names (see seller-agent CREW_MCP_TOOLS / mcp_server).
_TOOL_LIST_PRODUCTS = "list_products"
_TOOL_GET_PRODUCT = "get_product_details"
_TOOL_DISCOVER = "discover_inventory"


class AgentCoreSellerProxy:
    """OpenDirect-shaped client backed by InvokeAgentRuntime (seller MCP runtime).

    Only the read surface the planning flow exercises is implemented
    (``list_products``, ``list_products_tolerant``, ``get_product``,
    ``search_products``, ``check_avails``) plus ``close`` and the async context
    manager, matching how ``DealBookingFlow`` / the research crews use the
    client. Booking (orders/lines/deals) is not on the planning path and is not
    proxied here.

    Args:
        runtime_arn: The seller **MCP** AgentCore runtime ARN.
        region: AWS region of the runtime.
        session_id: Optional runtime session id for multi-call continuity.
        client: Optional pre-built boto3 ``bedrock-agentcore`` client (tests
            inject a stub; production builds one lazily).
    """

    def __init__(
        self,
        runtime_arn: str,
        *,
        region: str | None = None,
        session_id: str | None = None,
        client: Any = None,
        token_provider: Any = None,
        token_endpoint: str = "",
        scope: str = "",
    ) -> None:
        self._arn = runtime_arn
        self._region = region
        self._session_id = session_id
        self._client = client
        self._mcp_id = 0
        self._mcp_session_id: str | None = None
        self._initialized = False
        # JWT/HTTPS transport mode (AWS Bedrock AgentCore CUSTOM_JWT path): when
        # a token provider is supplied, the seller runtime enforces a JWT
        # authorizer, so SigV4/boto3 is DISALLOWED — MCP JSON-RPC is sent over a
        # raw HTTPS POST to the verified invocations URL with a Bearer token
        # (re-minted once on a 401). Otherwise the legacy SigV4 boto3 path runs.
        self._token_provider = token_provider
        self._token_endpoint = token_endpoint
        self._scope = scope
        self._use_jwt = token_provider is not None

    # -- boto3 client (lazy) ---------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3  # local import: only needed on the ARN path

            self._client = boto3.client("bedrock-agentcore", region_name=self._region)
        return self._client

    # -- InvokeAgentRuntime + MCP framing --------------------------------------

    def _next_id(self) -> int:
        self._mcp_id += 1
        return self._mcp_id

    # AgentCore MCP runtimes speak Streamable-HTTP: the /mcp path is a
    # pass-through of the InvokeAgentRuntime payload, but the MCP transport
    # REQUIRES the dual Accept header and a JSON content type, and expects the
    # standard initialize -> notifications/initialized handshake before any
    # tools/call. boto3 exposes these as first-class header params (accept,
    # contentType, mcpProtocolVersion, mcpSessionId). Omitting the Accept
    # header yields HTTP 406 (McpRequestUnacceptableException, -32011).
    _MCP_ACCEPT = "application/json, text/event-stream"
    _MCP_CONTENT_TYPE = "application/json"
    _MCP_PROTOCOL_VERSION = "2025-03-26"

    def _invoke_mcp(self, payload: dict[str, Any], *, expect_body: bool = True) -> bytes:
        """One InvokeAgentRuntime call carrying an MCP JSON-RPC message.

        SigV4/boto3 by default; a raw HTTPS POST with a Bearer JWT when the
        runtime enforces CUSTOM_JWT (``_use_jwt``). Both set the MCP-required
        headers, thread the runtime + MCP session ids for microVM stickiness,
        and capture the platform ``Mcp-Session-Id`` for reuse. Returns the raw
        response body (empty for notifications).
        """
        if self._use_jwt:
            return self._invoke_mcp_jwt(payload, expect_body=expect_body)

        kwargs: dict[str, Any] = {
            "agentRuntimeArn": self._arn,
            "payload": json.dumps(payload).encode("utf-8"),
            "accept": self._MCP_ACCEPT,
            "contentType": self._MCP_CONTENT_TYPE,
            "mcpProtocolVersion": self._MCP_PROTOCOL_VERSION,
        }
        if self._session_id:
            kwargs["runtimeSessionId"] = self._session_id
        if self._mcp_session_id:
            kwargs["mcpSessionId"] = self._mcp_session_id

        resp = self._get_client().invoke_agent_runtime(**kwargs)

        # Capture the platform-issued MCP session id (header) for affinity.
        sid = (resp.get("mcpSessionId") if isinstance(resp, dict) else None) or (
            resp.get("ResponseMetadata", {})
            .get("HTTPHeaders", {})
            .get("mcp-session-id")
            if isinstance(resp, dict)
            else None
        )
        if sid:
            self._mcp_session_id = sid

        if not expect_body:
            return b""
        body = resp["response"]
        return body.read() if hasattr(body, "read") else bytes(body)

    def _invoke_mcp_jwt(self, payload: dict[str, Any], *, expect_body: bool = True) -> bytes:
        """MCP JSON-RPC over a raw HTTPS POST with a Bearer JWT (CUSTOM_JWT path).

        boto3/SigV4 is DISALLOWED for OAuth runtimes (per the AgentCore docs), so
        we POST directly to the verified invocations URL. A 401 triggers a single
        reactive re-mint before failing.
        """
        import httpx

        from ad_buyer.registry.transport_selector import agentcore_invocations_url

        url = agentcore_invocations_url(self._arn) if self._arn.startswith("arn:") else self._arn

        def _post(force_token: bool) -> httpx.Response:
            token = self._token_provider.get_token(
                self._token_endpoint, self._scope, force=force_token
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": self._MCP_ACCEPT,
                "Content-Type": self._MCP_CONTENT_TYPE,
            }
            if self._mcp_session_id:
                headers["Mcp-Session-Id"] = self._mcp_session_id
            with httpx.Client(timeout=120.0) as client:
                return client.post(
                    url,
                    content=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                )

        resp = _post(force_token=False)
        if resp.status_code == 401:
            # Cached token rejected — drop it and re-mint once.
            self._token_provider.invalidate(self._token_endpoint, self._scope)
            resp = _post(force_token=True)

        sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
        if sid:
            self._mcp_session_id = sid

        if not expect_body:
            return b""
        return resp.content

    def _do_initialize(self) -> None:
        """Run the MCP initialize + notifications/initialized handshake (sync)."""
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": self._MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ad_buyer.AgentCoreSellerProxy", "version": "1"},
            },
        }
        raw = self._invoke_mcp(init_payload)
        # Surface a hard protocol/auth error early (406/403 etc. arrive as text).
        _raise_for_mcp_error(raw)
        # Ack — a notification has no id and expects no body.
        self._invoke_mcp(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            expect_body=False,
        )
        self._initialized = True

    async def _call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke one MCP ``tools/call`` on the seller runtime, return parsed data.

        Ensures the MCP session is initialized, then issues the ``tools/call``
        with the required MCP headers; the streamed response body is parsed back
        into the tool's structured result.
        """
        import asyncio

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }

        def _invoke() -> bytes:
            if not self._initialized:
                self._do_initialize()
            return self._invoke_mcp(payload)

        raw = await asyncio.get_event_loop().run_in_executor(None, _invoke)
        return _parse_mcp_tool_result(raw)

    # -- OpenDirect surface ----------------------------------------------------

    async def list_products(self, skip: int = 0, top: int = 50, **filters: Any) -> list[Product]:
        data = await self._call_tool(_TOOL_LIST_PRODUCTS, {"limit": top})
        products = _extract_products(data)
        if filters:
            products = _apply_filters(products, filters)
        return products[:top]

    async def list_products_tolerant(
        self, skip: int = 0, top: int = 50, **filters: Any
    ) -> tuple[list[Product], list[dict[str, Any]]]:
        """Tolerant variant: per-product mapping, collecting rejects.

        Mirrors ``OpenDirectClient.list_products_tolerant`` so the per-seller
        catalog-resolution path (which prefers the tolerant call) works
        unchanged over the proxy.
        """
        data = await self._call_tool(_TOOL_LIST_PRODUCTS, {"limit": top})
        raw_items = data.get("products", []) if isinstance(data, dict) else []
        products: list[Product] = []
        rejects: list[dict[str, Any]] = []
        for item in raw_items:
            try:
                products.append(_map_product(item))
            except Exception as exc:  # noqa: BLE001 — collect, don't fail the catalog
                rejects.append(
                    {
                        "product_id": item.get("product_id") if isinstance(item, dict) else None,
                        "name": item.get("name") if isinstance(item, dict) else None,
                        "reason": str(exc),
                    }
                )
        if filters:
            products = _apply_filters(products, filters)
        return products[:top], rejects

    async def get_product(self, product_id: str) -> Product:
        data = await self._call_tool(_TOOL_GET_PRODUCT, {"product_id": product_id})
        # get_product_details may return a bare product or {"product": {...}}.
        item = data.get("product", data) if isinstance(data, dict) else data
        return _map_product(item)

    async def search_products(self, filters: dict[str, Any]) -> list[Product]:
        data = await self._call_tool(_TOOL_LIST_PRODUCTS, {"limit": 500})
        return _apply_filters(_extract_products(data), filters or {})

    async def check_avails(self, request: AvailsRequest) -> AvailsResponse:
        product_id = getattr(request, "product_id", None) or getattr(request, "productId", None)
        data = await self._call_tool(
            _TOOL_DISCOVER,
            {"product_id": product_id} if product_id else {},
        )
        return AvailsResponse.model_validate(_avails_payload(data))

    async def close(self) -> None:  # noqa: D401 — parity with OpenDirectClient
        """No persistent connection to close (boto3 client is stateless here)."""
        return None

    async def __aenter__(self) -> AgentCoreSellerProxy:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# -- module-level parsing helpers (unit-testable without AWS) ------------------


def _decode_jsonrpc_body(raw: bytes | str) -> dict | list | None:
    """Decode an MCP response body that may be raw JSON or SSE-framed.

    AgentCore MCP runtimes reply with ``Content-Type: text/event-stream`` (we
    send ``Accept: application/json, text/event-stream``), so the JSON-RPC
    envelope arrives as Server-Sent Events frames::

        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}

    This extracts and parses the ``data:`` payload. A plain-JSON body (no SSE
    framing) is parsed directly. Returns the parsed envelope, or ``None`` if the
    body is empty / unparseable (callers decide whether that is an error).
    """
    if not raw:
        return None
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    stripped = text.lstrip()
    # Fast path: already JSON.
    if stripped[:1] in "{[":
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    # SSE framing: concatenate all `data:` lines (last complete JSON wins).
    data_lines = [
        line[len("data:") :].strip()
        for line in text.splitlines()
        if line.startswith("data:")
    ]
    for chunk in reversed(data_lines):
        try:
            return json.loads(chunk)
        except ValueError:
            continue
    return None


def _raise_for_mcp_error(raw: bytes) -> None:
    """Raise if a raw MCP response body carries a JSON-RPC ``error`` object.

    AgentCore returns most MCP errors as HTTP 200 with a JSON-RPC error in the
    body (e.g. -32011 Accept-header/406, -32603 internal). boto3 raises for the
    genuine HTTP-level 4xx/5xx itself; this catches the in-body errors so the
    handshake fails loudly instead of proceeding against a dead session.
    """
    env = _decode_jsonrpc_body(raw)
    if isinstance(env, dict) and env.get("error"):
        raise ValueError(f"seller MCP protocol error: {env['error']}")


def _parse_mcp_tool_result(raw: bytes) -> Any:
    """Parse an MCP ``tools/call`` response body into the tool's structured data.

    The MCP result shape is ``{"result": {"content": [{"type": "text",
    "text": "<json>"}], "isError": false}}``. The tool's real payload is the
    JSON string inside the first text content block. Also tolerates a bare
    JSON-RPC ``{"result": <data>}`` or a plain object. The transport body may be
    raw JSON or SSE-framed (see ``_decode_jsonrpc_body``).
    """
    envelope = _decode_jsonrpc_body(raw)
    if envelope is None:
        raw_txt = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))[:200]
        raise ValueError(f"seller MCP response was not JSON/SSE: {raw_txt!r}")

    if isinstance(envelope, dict) and envelope.get("error"):
        raise ValueError(f"seller MCP error: {envelope['error']}")

    result = envelope.get("result", envelope) if isinstance(envelope, dict) else envelope
    # MCP CallToolResult: content[0].text holds the tool's JSON payload.
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        for block in result["content"]:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                try:
                    return json.loads(block["text"])
                except ValueError:
                    return {"raw_text": block["text"]}
    return result


def _extract_products(data: Any) -> list[Product]:
    items = data.get("products", []) if isinstance(data, dict) else []
    out: list[Product] = []
    for item in items:
        try:
            out.append(_map_product(item))
        except Exception as exc:  # noqa: BLE001
            logger.warning("skipping unmappable product from seller MCP: %s", exc)
    return out


def _map_product(item: dict[str, Any]) -> Product:
    """Map a seller MCP product dict to the OpenDirect ``Product`` model.

    The seller MCP tools emit shared ``iab_agentic_primitives`` Product-shaped
    records, so the shared-wire mapper (``from_wire_product``) is the source of
    truth — the same boundary mapping ``OpenDirectClient`` uses. A record that
    does not validate as a shared Product raises; callers on the tolerant path
    (``list_products_tolerant`` / ``_extract_products``) collect the reject
    rather than failing the whole catalog.
    """
    from iab_agentic_primitives.primitives import Product as WireProduct

    return from_wire_product(WireProduct.model_validate(item))


def _apply_filters(products: list[Product], filters: dict[str, Any]) -> list[Product]:
    """Client-side filtering (parity with OpenDirectClient's client-side filter)."""
    if not filters:
        return products
    out = products
    inv = filters.get("inventory_type") or filters.get("channel")
    if inv:
        out = [p for p in out if getattr(p, "inventory_type", None) == inv]
    return out


def _avails_payload(data: Any) -> dict[str, Any]:
    """Normalize a discover_inventory result into an AvailsResponse dict."""
    if isinstance(data, dict) and "available" in data:
        return data
    # Minimal shape when the discover tool returns a summary/list.
    return {"available": True, "products": data if isinstance(data, list) else []}
