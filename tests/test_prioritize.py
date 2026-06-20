"""Tests for prioritize (per review: mocks, sample data)."""
from unittest.mock import AsyncMock

import pytest

from garvis.config import Config
from garvis.gather import Item
from garvis.prioritize import prioritize


@pytest.mark.asyncio
async def test_prioritize_basic(tmp_path):
    cfg = Config.load("config.example.yaml")
    cfg.raw["dry_run"] = True

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value.content = "Focus: Reply to boss about meeting."

    actionable = [
        Item(source="gmail", id="1", subject="Meeting?", sender="boss@ex.com", date="2026-06-14", snippet="Confirm?", thread_id="t1", label="ACTIONABLE"),
    ]
    waiting = []

    result = await prioritize(mock_llm, actionable, waiting, "Monday, June 16, 2026")

    assert "Focus" in result or "boss" in result.lower()
    assert len(result) > 10  # basic sanity for non-empty briefing
