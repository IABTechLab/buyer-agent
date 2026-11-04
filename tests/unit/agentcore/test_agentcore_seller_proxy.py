"""Unit tests for AgentCoreSellerProxy (offline — boto3 client is stubbed).

Verifies the proxy presents the OpenDirect surface backed by a mocked
InvokeAgentRuntime returning MCP tools/call envelopes, and that the MCP
result parsing / product mapping helpers behave.
"""

import json

import pytest

from ad_buyer.interfaces.agentcore.agentcore_seller_proxy import (
    AgentCoreSellerProxy,
    _parse_mcp_tool_result,
)

ARN = "arn:aws:bedrock-agentcore:us-west-2:111122223333:runtime/seller_mcp-abc123"


def _wire_product(pid: str, name: str = "Apex CTV") -> dict:
    return {
        "product_id": pid,
        "seller_organization_id": "org-seller-1",
        "name": name,
        "base_price": {"amount_micros": 45_000_000, "currency": "USD"},
        "pricing_type": "floor",
        "pricing_model": "cpm",
    }


def _mcp_envelope(payload: dict) -> bytes:
    """Wrap a tool payload as an MCP CallToolResult JSON-RPC response body."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": False,
            },
        }
    ).encode("utf-8")


class _StreamBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeBedrockAgentCore:
    """Stub boto3 bedrock-agentcore client capturing the last invoke."""

    def __init__(self, response_payload: dict):
        self._payload = response_payload
        self.last_kwargs: dict | None = None

    def invoke_agent_runtime(self, **kwargs):
        self.last_kwargs = kwargs
        return {"response": _StreamBody(_mcp_envelope(self._payload))}


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


class TestParseMcpToolResult:
    def test_unwraps_content_text_json(self):
        raw = _mcp_envelope({"products": [{"product_id": "inv-ctv-1"}]})
        data = _parse_mcp_tool_result(raw)
        assert data == {"products": [{"product_id": "inv-ctv-1"}]}

    def test_raises_on_jsonrpc_error(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}}).encode()
        with pytest.raises(ValueError, match="seller MCP error"):
            _parse_mcp_tool_result(raw)

    def test_raises_on_non_json(self):
        with pytest.raises(ValueError, match="not JSON"):
            _parse_mcp_tool_result(b"<html>nope</html>")

    def test_unwraps_sse_framed_body(self):
        """AgentCore MCP replies as text/event-stream; parse the data: frame."""
        inner = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"products": [{"product_id": "inv-ctv-1"}]})}],
                    "isError": False,
                },
            }
        )
        sse = f"event: message\ndata: {inner}\n\n".encode("utf-8")
        data = _parse_mcp_tool_result(sse)
        assert data == {"products": [{"product_id": "inv-ctv-1"}]}


# --------------------------------------------------------------------------- #
# OpenDirect surface over the mocked runtime
# --------------------------------------------------------------------------- #


class _RecordingBedrockAgentCore:
    """Stub that records EVERY invoke (to assert the MCP handshake sequence)."""

    def __init__(self, response_payload: dict):
        self._payload = response_payload
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": _StreamBody(_mcp_envelope(self._payload))}


class TestMcpTransportContract:
    async def test_sets_required_mcp_accept_header(self):
        """Regression: omitting the dual Accept header returns HTTP 406 (-32011)."""
        fake = _RecordingBedrockAgentCore({"products": []})
        proxy = AgentCoreSellerProxy(ARN, client=fake)
        await proxy.list_products()
        for call in fake.calls:
            assert call["accept"] == "application/json, text/event-stream"
            assert call["contentType"] == "application/json"
            assert call["mcpProtocolVersion"] == "2025-03-26"

    async def test_initialize_handshake_precedes_tools_call(self):
        fake = _RecordingBedrockAgentCore({"products": []})
        proxy = AgentCoreSellerProxy(ARN, client=fake)
        await proxy.list_products()
        methods = [json.loads(c["payload"])["method"] for c in fake.calls]
        assert methods == ["initialize", "notifications/initialized", "tools/call"]

    async def test_initialize_runs_once_across_calls(self):
        fake = _RecordingBedrockAgentCore({"products": []})
        proxy = AgentCoreSellerProxy(ARN, client=fake)
        await proxy.list_products()
        await proxy.list_products()
        methods = [json.loads(c["payload"])["method"] for c in fake.calls]
        assert methods.count("initialize") == 1
        assert methods.count("tools/call") == 2


class TestListProducts:
    async def test_list_products_maps_wire_products(self):
        fake = _FakeBedrockAgentCore(
            {"products": [_wire_product("inv-ctv-apex-sports-nba"), _wire_product("inv-lin-gnn", "GNN")]}
        )
        proxy = AgentCoreSellerProxy(ARN, region="us-west-2", client=fake)
        products = await proxy.list_products(top=10)
        assert [p.name for p in products] == ["Apex CTV", "GNN"]
        # It invoked the seller by ARN via the MCP list_products tool.
        payload = json.loads(fake.last_kwargs["payload"])
        assert fake.last_kwargs["agentRuntimeArn"] == ARN
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "list_products"

    async def test_get_product(self):
        fake = _FakeBedrockAgentCore({"product": _wire_product("inv-ctv-apex-series", "Apex Series")})
        proxy = AgentCoreSellerProxy(ARN, client=fake)
        product = await proxy.get_product("inv-ctv-apex-series")
        assert product.name == "Apex Series"
        assert json.loads(fake.last_kwargs["payload"])["params"]["name"] == "get_product_details"

    async def test_list_products_tolerant_collects_rejects(self):
        # Second item is missing required fields -> reject, not fatal.
        fake = _FakeBedrockAgentCore(
            {"products": [_wire_product("inv-ok"), {"name": "broken — no ids"}]}
        )
        proxy = AgentCoreSellerProxy(ARN, client=fake)
        products, rejects = await proxy.list_products_tolerant(top=10)
        assert [p.name for p in products] == ["Apex CTV"]
        assert len(rejects) == 1
        assert rejects[0]["name"] == "broken — no ids"

    async def test_session_id_threaded(self):
        fake = _FakeBedrockAgentCore({"products": []})
        proxy = AgentCoreSellerProxy(ARN, client=fake, session_id="sess-1")
        await proxy.list_products()
        assert fake.last_kwargs["runtimeSessionId"] == "sess-1"
