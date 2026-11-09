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
import re
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx2  # httpx's successor, the HTTP client the MCP SDK is built on (not a typo)
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI

MCP_URL = os.getenv("DEXPAPRIKA_MCP_URL", "https://mcp.dexpaprika.com/streamable-http")
MCP_HOST = "mcp.dexpaprika.com"

# 30 s to connect or send, 300 s to read: the server can hold a response stream open.
TIMEOUT = httpx2.Timeout(30, read=300)
MODEL_TIMEOUT = 120  # seconds per model reply; the OpenAI SDK default is 600, too long for a loop

# Tool results can be long pool lists. Trimming keeps a small model's context usable.
MAX_TOOL_CHARS = int(os.getenv("MAX_TOOL_CHARS", "6000"))


def load_dotenv(path: str = ".env") -> None:
    """Read KEY=value lines from .env into the environment, without overriding real variables.

    A key set twice stops the script: with two model blocks left in, one of them would be
    ignored without a word, and the model call would fail for a reason nowhere in sight.
    """
    if not os.path.exists(path):
        return
    values: dict[str, tuple[int, str]] = {}
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if key in values:
                raise SystemExit(f"{key} is set twice in {path} (lines {values[key][0]} and {number}). "
                                 "Keep one model block and comment out the rest.")
            values[key] = (number, value.strip('"').strip("'"))
    for key, (_, value) in values.items():
        os.environ.setdefault(key, value)


@asynccontextmanager
async def dexpaprika() -> AsyncIterator[Client]:
    """An MCP session with the hosted DexPaprika server.

    Most tools work without a key. If DEXPAPRIKA_API_KEY is set, it is sent as the
    Authorization header, on its own, which unlocks the tools that need a Dev or Pro plan.
    """
    key = os.getenv("DEXPAPRIKA_API_KEY", "").strip()
    if key:
        check_mcp_url(MCP_URL)
    async with http_client(key) as http, Client(streamable_http_client(MCP_URL, http_client=http)) as client:
        yield client


def http_client(key: str = "") -> httpx2.AsyncClient:
    """The HTTP client under every MCP session: explicit timeouts and no redirects.

    Both are httpx2's defaults today; stating them means a dependency upgrade cannot
    quietly change either, and a redirect can never carry the key to another host.
    """
    headers = {"Authorization": key} if key else {}
    return httpx2.AsyncClient(headers=headers, timeout=TIMEOUT, follow_redirects=False)


def check_mcp_url(url: str) -> None:
    """Refuse to send a DexPaprika key anywhere but the DexPaprika MCP server over HTTPS."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != MCP_HOST:
        raise SystemExit(
            f"DEXPAPRIKA_API_KEY is only sent to https://{MCP_HOST}. "
            f"Unset DEXPAPRIKA_MCP_URL or unset the key. Got: {parts.scheme}://{parts.hostname}"
        )


# Control characters and bidirectional overrides. Token names and symbols are set by
# whoever deployed the token, so they are cleaned before they reach a terminal or a chat.
_UNSAFE = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]")


def clean(text: Any) -> str:
    return _UNSAFE.sub("", "" if text is None else str(text))


# Characters Discord reads as markup or as a masked link: [text](url), **bold**, `code`.
_MARKUP = re.compile(r"[\\\[\]()<>*_~`|#]")


def label(text: Any, limit: int = 40) -> str:
    """A token symbol or DEX name made safe to post: cleaned, no markup, no link, short.

    "[Claim airdrop](https://evil.test)" as a token name would otherwise post as a
    clickable link in Discord, and Telegram links any URL it sees.
    """
    text = _MARKUP.sub("", clean(text)).replace("://", ": //")
    return " ".join(text.split())[:limit]


def args(doc: str, flags: tuple[str, ...] = (), free_text: bool = False) -> list[str]:
    """The command-line arguments, after handling --help and refusing unknown flags.

    `doc` is the script's docstring, printed for -h or --help. With `free_text`, anything
    that is not a help flag is the user's text (market_chat.py takes a question).
    """
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        print(doc.strip())
        raise SystemExit(0)
    if not free_text:
        for a in argv:
            if a not in flags:
                raise SystemExit(f"Unknown argument {a!r}. Run with --help for usage.")
    return argv


def model() -> tuple[AsyncOpenAI, str]:
    """The chat model client and model name, from LLM_BASE_URL, LLM_API_KEY and LLM_MODEL."""
    name = os.getenv("LLM_MODEL", "").strip()
    if not name:
        raise SystemExit("Set LLM_MODEL (and LLM_BASE_URL, LLM_API_KEY). See .env.example.")
    client = AsyncOpenAI(
        base_url=os.getenv("LLM_BASE_URL") or None,
        api_key=os.getenv("LLM_API_KEY") or "not-needed-for-local-models",
        timeout=MODEL_TIMEOUT,
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

    The model can only run the tools it was given. Tool results are data from the
    internet (token names, for one, are set by whoever deployed the token), so a model
    that reads an instruction in them still cannot reach anything outside that list.
    Tool results and the answer pass through `clean()`, so an escape code in a token name
    reaches neither the model nor the terminal.
    """
    llm, name = model()
    allowed = {t["function"]["name"] for t in tools}
    for _ in range(max_rounds):
        reply = await llm.chat.completions.create(model=name, messages=messages, tools=tools)
        msg = reply.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return clean(msg.content)
        for tc in msg.tool_calls:
            if tc.function.name not in allowed:
                text = f"error: {tc.function.name} is not one of the tools you were given."
            else:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = await mcp.call_tool(tc.function.name, args)
                    text = "".join(getattr(c, "text", "") for c in result.content)
                except Exception as e:  # a bad argument is the model's to fix, so it sees the error
                    text = f"error: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": clean(text)[:MAX_TOOL_CHARS]})
    return "Stopped after too many tool calls. Try a narrower question."
