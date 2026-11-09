"""Re-record tests/fixtures from the live DexPaprika MCP server.

Run it from the repo root after the server changes a tool's output, then run the tests:

    python scripts/record_fixtures.py
    python -m unittest

It makes 7 tool calls and writes each result exactly as the server sent it.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentkit import dexpaprika  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
WHY = "Recording fixtures for the DexPaprika agent kit's offline tests."
SIGNAL_POOL = {"network": "ethereum", "pool_address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"}  # USDC/WETH, Uniswap V3


async def record(mcp, name: str, tool: str, args: dict) -> str:
    result = await mcp.call_tool(tool, {**args, "rationale": WHY})
    text = "".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        raise SystemExit(f"{tool} failed: {text[:300]}")
    json.loads(text)  # refuse to save anything that is not JSON
    (FIXTURES / name).write_text(text)
    print(f"wrote {name}")
    return text


async def main() -> None:
    async with dexpaprika() as mcp:
        pools = await record(mcp, "getNetworkPools-base.json", "getNetworkPools",
                             {"network": "base", "sort_by": "created_at", "sort_dir": "desc", "limit": 3})
        newest = json.loads(pools)["results"][0]["id"]
        await record(mcp, "getPoolDetails-base.json", "getPoolDetails", {"network": "base", "pool_address": newest})
        await record(mcp, "getPoolDetails-ethereum-v3.json", "getPoolDetails", SIGNAL_POOL)
        for ind in ("rsi", "macd", "bbands"):
            await record(mcp, f"getPoolIndicator-{ind}.json", "getPoolIndicator",
                         {**SIGNAL_POOL, "indicator": ind, "interval": "1h", "limit": 2})
        await record(mcp, "getPoolOHLCV-ethereum-v3.json", "getPoolOHLCV", {**SIGNAL_POOL, "start": "-24h", "interval": "1h"})


if __name__ == "__main__":
    asyncio.run(main())
