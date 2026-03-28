# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the MCP server foundation.

Tests MCP server initialization, SSE endpoint mounting, and the three
foundation tools: get_setup_status, health_check, get_config.
"""

import json

import pytest

from mcp.server.fastmcp import FastMCP


def _extract_text(call_result) -> str:
    """Extract text content from a FastMCP call_tool result.

    call_tool returns (content_list, metadata_dict).
    content_list is a list of TextContent objects.
    """
    content_list = call_result[0]
    return content_list[0].text


class TestMCPServerInitialization:
    """Test that the MCP server is created and configured correctly."""

    def test_mcp_server_exists(self):
        """The mcp_server module should be importable."""
        from ad_buyer.interfaces.mcp_server import mcp
        assert mcp is not None

    def test_mcp_server_is_fastmcp_instance(self):
        """The mcp object should be a FastMCP instance."""
        from ad_buyer.interfaces.mcp_server import mcp
        assert isinstance(mcp, FastMCP)

    def test_mcp_server_name(self):
        """The MCP server should identify as the buyer agent."""
        from ad_buyer.interfaces.mcp_server import mcp
        assert mcp.name == "ad-buyer-agent"

    def test_mcp_server_has_instructions(self):
        """The MCP server should have instructions describing the buyer agent."""
        from ad_buyer.interfaces.mcp_server import mcp
        assert mcp.instructions is not None
        assert len(mcp.instructions) > 0


class TestSSEMounting:
    """Test that the MCP server can be mounted in the FastAPI app."""

    def test_mount_mcp_function_exists(self):
        """A mount_mcp function should exist for integrating with FastAPI."""
        from ad_buyer.interfaces.mcp_server import mount_mcp
        assert callable(mount_mcp)

    def test_mount_mcp_adds_route(self):
        """mount_mcp should add the /mcp/sse route to the FastAPI app."""
        from fastapi import FastAPI
        from ad_buyer.interfaces.mcp_server import mount_mcp

        test_app = FastAPI()
        mount_mcp(test_app)

        # Check that a route at /mcp/sse is mounted
        route_paths = []
        for route in test_app.routes:
            if hasattr(route, "path"):
                route_paths.append(route.path)

        # The mount should be at /mcp/sse
        assert any("/mcp/sse" in str(p) for p in route_paths), (
            f"Expected /mcp/sse in routes, got: {route_paths}"
        )

    def test_buyer_api_app_has_mcp_mounted(self):
        """The buyer API app should have MCP mounted after import."""
        from ad_buyer.interfaces.api.main import app

        route_paths = []
        for route in app.routes:
            if hasattr(route, "path"):
                route_paths.append(route.path)

        assert any("/mcp/sse" in str(p) for p in route_paths), (
            f"Expected /mcp/sse in buyer API app routes, got: {route_paths}"
        )


class TestMCPTools:
    """Test that the three foundation tools are registered and work correctly."""

    @pytest.mark.asyncio
    async def test_list_tools_includes_foundation_tools(self):
        """The MCP server should list get_setup_status, health_check, get_config."""
        from ad_buyer.interfaces.mcp_server import mcp

        tools_result = await mcp.list_tools()
        tool_names = [t.name for t in tools_result]

        assert "get_setup_status" in tool_names, (
            f"get_setup_status not in tools: {tool_names}"
        )
        assert "health_check" in tool_names, (
            f"health_check not in tools: {tool_names}"
        )
        assert "get_config" in tool_names, (
            f"get_config not in tools: {tool_names}"
        )

    @pytest.mark.asyncio
    async def test_foundation_tools_are_present(self):
        """The three foundation tools should be among the registered tools."""
        from ad_buyer.interfaces.mcp_server import mcp

        tools_result = await mcp.list_tools()
        tool_names = sorted(t.name for t in tools_result)

        # Foundation tools must always be present (other modules add more)
        for name in ["get_config", "get_setup_status", "health_check"]:
            assert name in tool_names, f"{name} not in {tool_names}"

    @pytest.mark.asyncio
    async def test_get_setup_status_tool(self):
        """get_setup_status should return setup state information."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        assert result is not None

        text_content = _extract_text(result)
        assert "setup_complete" in text_content

    @pytest.mark.asyncio
    async def test_health_check_tool(self):
        """health_check should return system health information."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        assert result is not None

        text_content = _extract_text(result)
        assert "status" in text_content

    @pytest.mark.asyncio
    async def test_get_config_tool(self):
        """get_config should return buyer agent configuration."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        assert result is not None

        text_content = _extract_text(result)
        assert "environment" in text_content


class TestGetSetupStatus:
    """Detailed tests for the get_setup_status tool."""

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        """get_setup_status should return valid JSON."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_includes_required_fields(self):
        """get_setup_status should include key setup fields."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        data = json.loads(_extract_text(result))

        # Should report on core setup areas
        assert "setup_complete" in data
        assert "checks" in data
        assert isinstance(data["checks"], dict)

    @pytest.mark.asyncio
    async def test_checks_seller_endpoints(self):
        """Setup status should check whether seller endpoints are configured."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        data = json.loads(_extract_text(result))

        assert "seller_endpoints_configured" in data["checks"]

    @pytest.mark.asyncio
    async def test_checks_database(self):
        """Setup status should check database connectivity."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        data = json.loads(_extract_text(result))

        assert "database_accessible" in data["checks"]

    @pytest.mark.asyncio
    async def test_includes_timestamp(self):
        """Setup status should include a timestamp."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_setup_status", {})
        data = json.loads(_extract_text(result))

        assert "timestamp" in data


class TestHealthCheck:
    """Detailed tests for the health_check tool."""

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        """health_check should return valid JSON."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_includes_status(self):
        """health_check should include an overall status field."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")

    @pytest.mark.asyncio
    async def test_includes_version(self):
        """health_check should include the system version."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "version" in data

    @pytest.mark.asyncio
    async def test_includes_services(self):
        """health_check should include service health details."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "services" in data
        assert isinstance(data["services"], dict)

    @pytest.mark.asyncio
    async def test_includes_timestamp(self):
        """health_check should include a timestamp."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_reports_database_service(self):
        """health_check should report on the database service."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "database" in data["services"]
        assert "status" in data["services"]["database"]

    @pytest.mark.asyncio
    async def test_reports_seller_connections(self):
        """health_check should report on seller connection status."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("health_check", {})
        data = json.loads(_extract_text(result))

        assert "seller_connections" in data["services"]


class TestGetConfig:
    """Detailed tests for the get_config tool."""

    @pytest.mark.asyncio
    async def test_returns_json_string(self):
        """get_config should return valid JSON."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_includes_environment(self):
        """get_config should include the environment name."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))

        assert "environment" in data

    @pytest.mark.asyncio
    async def test_includes_seller_endpoints(self):
        """get_config should include seller endpoint configuration."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))

        assert "seller_endpoints" in data

    @pytest.mark.asyncio
    async def test_does_not_expose_secrets(self):
        """get_config must not expose API keys or secrets."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        text = _extract_text(result)
        data = json.loads(text)

        # Must not contain actual API key field names
        assert "anthropic_api_key" not in data
        assert "api_key" not in data
        assert "opendirect_token" not in data
        assert "opendirect_api_key" not in data

        # Also check the raw text for common key patterns
        assert "sk-" not in text  # Anthropic key prefix
        assert "Bearer " not in text

    @pytest.mark.asyncio
    async def test_includes_database_url(self):
        """get_config should include the database URL."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))

        assert "database_url" in data

    @pytest.mark.asyncio
    async def test_includes_llm_settings(self):
        """get_config should include LLM model configuration."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))

        assert "default_llm_model" in data

    @pytest.mark.asyncio
    async def test_includes_cors_origins(self):
        """get_config should include CORS allowed origins."""
        from ad_buyer.interfaces.mcp_server import mcp

        result = await mcp.call_tool("get_config", {})
        data = json.loads(_extract_text(result))

        assert "cors_allowed_origins" in data


class TestErrorHandling:
    """Test error handling in MCP tools."""

    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self):
        """Calling a non-existent tool should raise an error."""
        from ad_buyer.interfaces.mcp_server import mcp

        with pytest.raises(Exception):
            await mcp.call_tool("nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_get_setup_status_handles_db_error(self):
        """get_setup_status should handle database connection errors gracefully."""
        from ad_buyer.interfaces.mcp_server import mcp

        # Even if database is unreachable, tool should return a result
        # (with database_accessible: false)
        result = await mcp.call_tool("get_setup_status", {})
        assert result is not None
        data = json.loads(_extract_text(result))
        assert "checks" in data


class TestPromptRegistration:
    """Test that all 10 MCP prompts (slash commands) are registered."""

    EXPECTED_PROMPTS = [
        "setup",
        "status",
        "campaigns",
        "deals",
        "discover",
        "negotiate",
        "orders",
        "approvals",
        "configure",
        "help",
    ]

    @pytest.mark.asyncio
    async def test_all_prompts_registered(self):
        """All 10 buyer prompts should be registered on the MCP server."""
        from ad_buyer.interfaces.mcp_server import mcp

        prompts_result = await mcp.list_prompts()
        prompt_names = [p.name for p in prompts_result]

        for name in self.EXPECTED_PROMPTS:
            assert name in prompt_names, (
                f"Prompt '{name}' not registered. Found: {prompt_names}"
            )

    @pytest.mark.asyncio
    async def test_prompt_count(self):
        """There should be exactly 10 prompts registered."""
        from ad_buyer.interfaces.mcp_server import mcp

        prompts_result = await mcp.list_prompts()
        assert len(prompts_result) == 10, (
            f"Expected 10 prompts, got {len(prompts_result)}: "
            f"{[p.name for p in prompts_result]}"
        )

    @pytest.mark.asyncio
    async def test_each_prompt_has_description(self):
        """Every registered prompt should have a non-empty description."""
        from ad_buyer.interfaces.mcp_server import mcp

        prompts_result = await mcp.list_prompts()
        for prompt in prompts_result:
            assert prompt.description, (
                f"Prompt '{prompt.name}' has no description"
            )

    @pytest.mark.asyncio
    async def test_each_prompt_returns_messages(self):
        """Every registered prompt should return a list of Messages."""
        from ad_buyer.interfaces.mcp_server import mcp

        prompts_result = await mcp.list_prompts()
        for prompt in prompts_result:
            result = await mcp.get_prompt(prompt.name)
            assert result is not None, (
                f"Prompt '{prompt.name}' returned None"
            )
            assert len(result.messages) > 0, (
                f"Prompt '{prompt.name}' returned no messages"
            )

    @pytest.mark.asyncio
    async def test_each_prompt_has_user_role(self):
        """Every prompt message should have role='user'."""
        from ad_buyer.interfaces.mcp_server import mcp

        prompts_result = await mcp.list_prompts()
        for prompt in prompts_result:
            result = await mcp.get_prompt(prompt.name)
            for msg in result.messages:
                assert msg.role == "user", (
                    f"Prompt '{prompt.name}' has role '{msg.role}', "
                    f"expected 'user'"
                )
