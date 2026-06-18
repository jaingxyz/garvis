"""Wire the local stdio MCP servers into LangChain and expose a simple call() API."""
from __future__ import annotations

import json
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from .config import Config


class Tools:
    """Thin wrapper over the LangChain MCP tools: call by raw tool name."""

    def __init__(self, tools: list):
        self.by_name = {t.name: t for t in tools}

    def names(self) -> list[str]:
        return sorted(self.by_name)

    async def call(self, tool_name: str, **kwargs) -> Any:
        if tool_name not in self.by_name:
            raise KeyError(f"MCP tool {tool_name!r} not found. Available: {self.names()}")
        result = await self.by_name[tool_name].ainvoke(kwargs)
        return _unwrap(result)


def _unwrap(result: Any) -> Any:
    """Normalize an MCP tool result to parsed JSON.

    The LangChain MCP adapter returns a list of content blocks, e.g.
    [{"type": "text", "text": "<json>"}]. Join the text blocks and parse.
    """
    if isinstance(result, list):
        texts = [
            b["text"] for b in result
            if isinstance(b, dict) and b.get("type") == "text" and "text" in b
        ]
        if texts:
            return _parse("\n".join(texts))
        return result
    if isinstance(result, str):
        return _parse(result)
    return result


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


async def connect(cfg: Config) -> Tools:
    client = MultiServerMCPClient(cfg.server_connections())
    tools = await client.get_tools()
    return Tools(tools)
