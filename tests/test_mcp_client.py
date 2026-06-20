"""Tests for mcp_client (per review: mocked Tools, connection)."""
from unittest.mock import AsyncMock, patch

import pytest

from garvis.config import Config
from garvis.mcp_client import Tools, connect


@pytest.mark.asyncio
async def test_tools_wrapper():
    mock_tool = AsyncMock()
    mock_tool.name = "gmail_list_recent"
    mock_tool.ainvoke.return_value = [{"type": "text", "text": '{"messages": []}'}]

    tools = Tools([mock_tool])
    assert "gmail_list_recent" in tools.names()

    result = await tools.call("gmail_list_recent", limit=5)
    assert result == {"messages": []}


@pytest.mark.asyncio
async def test_connect_mocked(tmp_path):
    cfg = Config.load("config.example.yaml")
    cfg.raw["mcp_servers"] = {
        "test": {"command": "echo", "args": [], "env": {}, "transport": "stdio"}
    }

    # Mock the adapter
    with patch("garvis.mcp_client.MultiServerMCPClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.get_tools.return_value = []
        mock_client.return_value = mock_instance

        tools = await connect(cfg)
        assert tools.names() == []
