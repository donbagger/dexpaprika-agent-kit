"""Run every agent's DexPaprika calls against the live server, without a model.

CI runs this every day so a change on the server side shows up here before it shows up in
someone's fork. Exits non-zero if anything no longer works.

    python smoke.py
"""

import asyncio
import os
import sys

import market_chat
import pool_radar
import signal_agent
from agentkit import dexpaprika, load_dotenv


failures: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


async def main() -> None:
    load_dotenv()
    for name in ("LLM_MODEL", "DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN"):
        os.environ.pop(name, None)  # no model, no posting: this checks the data side only

    async with dexpaprika() as mcp:
        names = {t.name for t in (await mcp.list_tools()).tools}
        missing = sorted(set(market_chat.TOOLS) - names)
        check(not missing, f"market-chat tools all on the server {missing or ''}")

        seen: dict[str, None] = {}
        await pool_radar.scan(mcp, "solana", 0, seen)
        check(len(seen) > 0, f"pool-radar sees {len(seen)} new pools on solana")

        for network, pool in (p.split(":", 1) for p in signal_agent.DEFAULT_POOLS.split(",")):
            s = await signal_agent.snapshot(mcp, network, pool, "1h")
            d = signal_agent.rule(s)
            check(
                0 <= s["rsi"]["value"] <= 100 and s["close"] > 0 and d["signal"] in {"buy", "sell", "hold"},
                f"signal-agent {s['pair']} on {network}: rsi {s['rsi']['value']:.1f}, {d['signal']}",
            )


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(1 if failures else 0)
