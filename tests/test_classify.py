"""Tests for classify (per review priority on test coverage)."""
from unittest.mock import AsyncMock

import pytest

from garvis.classify import classify_item
from garvis.config import Config
from garvis.gather import Item


@pytest.mark.asyncio
async def test_classify_actionable(tmp_path):
    # Use a real-ish config
    cfg = Config.load("config.example.yaml")
    cfg.raw["dry_run"] = True

    # Mock LLM to return actionable
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value.content = '{"label": "ACTIONABLE", "reason": "Needs reply", "summary": "", "task": "reply", "deadline": ""}'

    item = Item(
        source="gmail",
        id="123",
        subject="Re: meeting?",
        sender="boss@example.com",
        date="2026-06-14T10:00:00",
        snippet="Can you confirm?",
        thread_id="t1",
    )

    await classify_item(mock_llm, cfg, "rules here", item)

    assert item.label == "ACTIONABLE"
    assert "Needs reply" in item.reason
