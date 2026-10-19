"""Shared plumbing for the agents in this kit.

Two connections, both configured from the environment:

- the hosted DexPaprika MCP server, which needs no install and no DexPaprika key
- any chat model behind an OpenAI-compatible API (Nebius Token Factory, OpenRouter,
  OpenAI, or a local Ollama), chosen with LLM_BASE_URL, LLM_API_KEY and LLM_MODEL

Each agent file imports from here and stays short enough to read in one sitting.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI

MCP_URL = os.getenv("DEXPAPRIKA_MCP_URL", "https://mcp.dexpaprika.com/streamable-http")

# Tool results can be long pool lists. Trimming keeps a small model's context usable.
MAX_TOOL_CHARS = int(os.getenv("MAX_TOOL_CHARS", "6000"))


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=value lines from .env into the environment, without overriding real variables."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@asynccontextmanager
async def dexpaprika() -> AsyncIterator[Client]:
    """An MCP session with the hosted DexPaprika server.

    Every tool works without a key. If DEXPAPRIKA_API_KEY is set, it is sent as the
    Authorization header, on its own, which unlocks the tools that need a Dev or Pro plan.
    """
    key = os.getenv("DEXPAPRIKA_API_KEY", "").strip()
    if not key:
        async with Client(MCP_URL) as client:
            yield client
        return
    http = httpx2.AsyncClient(headers={"Authorization": key}, timeout=httpx2.Timeout(30, read=300))
    async with http, Client(streamable_http_client(MCP_URL, http_client=http)) as client:
        yield client


def model() -> tuple[AsyncOpenAI, str]:
    """The chat model client and model name, from LLM_BASE_URL, LLM_API_KEY and LLM_MODEL."""
    name = os.getenv("LLM_MODEL", "").strip()
    if not name:
        raise SystemExit("Set LLM_MODEL (and LLM_BASE_URL, LLM_API_KEY). See .env.example.")
    client = AsyncOpenAI(
        base_url=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed-for-local-models",
    )
    return client, name


async def openai_tools(mcp: Client, only: list[str] | None = None) -> list[dict[str, Any]]:
    """The server's tools in the shape chat completions expects, optionally narrowed by name."""
    tools = (await mcp.list_tools()).tools
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in tools
        if only is None or t.name in only
    ]


async def call(mcp: Client, name: str, args: dict[str, Any]) -> Any:
    """Call one tool and return its result parsed as JSON when it is JSON, else the text.

    Every DexPaprika read tool takes a short `rationale`. Agents that call tools directly
    pass their own; this fills one in for the few that do not.
    """
    args = {"rationale": f"DexPaprika agent kit calling {name} for its scheduled task.", **args}
    result = await mcp.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        raise RuntimeError(f"{name} failed: {text[:300]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def chat(
    mcp: Client,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_rounds: int = 8,
) -> str:
    """Let the model call DexPaprika tools until it answers in text, and return that answer.

    `messages` is extended in place, so a caller can keep the conversation going.
    """
    llm, name = model()
    for _ in range(max_rounds):
        reply = await llm.chat.completions.create(model=name, messages=messages, tools=tools)
        msg = reply.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or ""
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = await mcp.call_tool(tc.function.name, args)
                text = "".join(getattr(c, "text", "") for c in result.content)
            except Exception as e:  # a bad argument is the model's to fix, so it sees the error
                text = f"error: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text[:MAX_TOOL_CHARS]})
    return "Stopped after too many tool calls. Try a narrower question."
