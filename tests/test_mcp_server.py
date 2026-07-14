"""Agent-facing MCP surface tests."""

from __future__ import annotations

import pytest

from gmaps import mcp_server


@pytest.mark.asyncio
async def test_mcp_exposes_named_location_collect_tool_when_sdk_is_installed() -> None:
    if not mcp_server._HAS_MCP:
        pytest.skip("optional MCP SDK is not installed")

    tools = await mcp_server.list_tools()
    by_name = {tool.name: tool for tool in tools}

    assert "collect" in by_name
    schema = by_name["collect"].inputSchema
    assert schema["required"] == ["query", "output"]
    assert "max_contacts" in schema["properties"]
    assert "contacts" in schema["properties"]
