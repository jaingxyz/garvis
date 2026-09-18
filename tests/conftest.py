"""Shared test doubles: a recording MCP tool stub and a minimal Config stand-in."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest


class FakeTools:
    """Records every call; returns `result` (or raises `raise_exc`) for each one.

    Same call signature as garvis.mcp_client.Tools.call, so keyword args such as `name=`
    pass through exactly as the real client receives them.
    """

    def __init__(self, result: Any = None, raise_exc: BaseException | None = None):
        self.result = {"ok": True} if result is None else result
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        self.calls.append((tool_name, kwargs))
        if self.raise_exc:
            raise self.raise_exc
        return self.result


class FakeConfig:
    """Just enough of garvis.config.Config for the guards/actions code paths."""

    def __init__(self, **raw: Any):
        self.raw = {"dry_run": False, **raw}

    @property
    def dry_run(self) -> bool:
        return bool(self.raw.get("dry_run", True))


def make_cfg(**raw: Any) -> FakeConfig:
    return FakeConfig(**raw)


def ago(**kw: float) -> str:
    """ISO timestamp this far in the past, for first_seen/date fields."""
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


@pytest.fixture
def tools() -> FakeTools:
    return FakeTools()
