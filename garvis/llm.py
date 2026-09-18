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


def _content_text(resp: Any) -> str:
    """Flatten a chat response's content to plain text.

    LangChain may return `content` as a string or as a list of text/content blocks;
    join the textual parts so the JSON parsing below always sees a str.
    """
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def ask_json(llm: ChatOllama, system: str, user: str, max_retries: int = 2) -> dict[str, Any]:
    """Ask the model and parse a single JSON object.

    Uses Ollama format=json for better structure. Retries with nudge on failure.
    Falls back defensively to {"_raw": text} (never crashes the sweep).
    """
    prompt = f"{user}\n\nRespond with ONLY a valid JSON object. No other text."
    print(f"[garvis thinking] JSON classify prompt (first 400 chars): {user[:400]}...")
    last_text = ""
    for attempt in range(max_retries + 1):
        resp = await llm.ainvoke([("system", system), ("human", prompt)])
        text = _content_text(resp)
        last_text = text
        print(f"[garvis thinking] LLM JSON response (attempt {attempt}): {text[:300]}...")
        # Prefer direct parse if model respected format
        data = _parse_json_object(text.strip())
        if data is not None:
            return data
        # Fallback regex
        m = _JSON_RE.search(text)
        if m:
            data = _parse_json_object(m.group(0))
            if data is not None:
                return data
        if attempt < max_retries:
            # Nudge for next attempt
            prompt = f"{user}\n\nYou MUST return ONLY valid JSON. Previous attempt was invalid."
    # Defensive fallback (preserves original behavior but safer)
    return {"_raw": last_text}


async def ask_text(llm: ChatOllama, system: str, user: str) -> str:
    print(f"[garvis thinking] text prompt (first 400 chars): {user[:400]}...")
    resp = await llm.ainvoke([("system", system), ("human", user)])
    text = _content_text(resp)
    print(f"[garvis thinking] LLM text response: {text[:400]}...")
    return text
