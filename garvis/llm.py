"""Local LLM (Ollama) helpers for the judgment steps.

Improvements for reliability (per core review):
- Use Ollama's `format="json"` for structured output (more reliable than regex).
- Simple retry with nudge on parse failure.
- Fallback to UNSURE on persistent failure (defensive).
- No Pydantic yet (added to pyproject test extras for future).
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_ollama import ChatOllama

from .config import Config

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_llm(cfg: Config, format: str | None = None) -> ChatOllama:
    """Build ChatOllama client.

    Pass format="json" for classify steps (leverages Ollama structured output).
    Leave None/default for free-text responses (e.g. prioritize briefing).
    """
    spec = cfg.llm
    kwargs = {
        "model": spec["model"],
        "base_url": spec.get("base_url", "http://localhost:11434"),
        "temperature": spec.get("temperature", 0),
    }
    if format is not None:
        kwargs["format"] = format
    return ChatOllama(**kwargs)


async def ask_json(llm: ChatOllama, system: str, user: str, max_retries: int = 2) -> dict[str, Any]:
    """Ask the model and parse a single JSON object.

    Uses Ollama format=json for better structure. Retries with nudge on failure.
    Falls back defensively to {"_raw": text} (never crashes the sweep).
    """
    prompt = f"{user}\n\nRespond with ONLY a valid JSON object. No other text."
    last_text = ""
    for attempt in range(max_retries + 1):
        resp = await llm.ainvoke([("system", system), ("human", prompt)])
        text = resp.content if hasattr(resp, "content") else str(resp)
        last_text = text
        # Prefer direct parse if model respected format
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        # Fallback regex
        m = _JSON_RE.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        if attempt < max_retries:
            # Nudge for next attempt
            prompt = f"{user}\n\nYou MUST return ONLY valid JSON. Previous attempt was invalid."
    # Defensive fallback (preserves original behavior but safer)
    return {"_raw": last_text}


async def ask_text(llm: ChatOllama, system: str, user: str) -> str:
    resp = await llm.ainvoke([("system", system), ("human", user)])
    return resp.content if hasattr(resp, "content") else str(resp)
