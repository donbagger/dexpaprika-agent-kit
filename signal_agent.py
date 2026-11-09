"""signal-agent: paper trading signals from RSI, MACD and Bollinger Bands.

For each pool on the watchlist it reads three indicators that DexPaprika computes
server-side, plus the latest candle, and asks the model for buy, sell or hold with a reason.
Without a model it applies a plain rule instead, so it always produces an answer.

This is an example of wiring indicators into an agent. It places no orders and is not
trading advice.

    python signal_agent.py
    SIGNAL_POOLS="ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640" python signal_agent.py

Settings (environment or .env):
    SIGNAL_POOLS      comma-separated network:pool_address pairs (default: two large pools)
    SIGNAL_INTERVAL   candle interval for the indicators, default 1h
"""

import asyncio
import json
import os

from agentkit import args, call, clean, dexpaprika, load_dotenv, model

DEFAULT_POOLS = (
    "ethereum:0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640,"  # USDC/WETH, Uniswap V3
    "solana:Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"   # SOL/USDC, Orca
)

SYSTEM = """You turn technical indicators for one DEX pool into a paper trading signal.
Reply with JSON only: {"signal": "buy" | "sell" | "hold", "reason": "<one sentence>"}.
Use only the numbers given. Mixed or weak evidence means hold."""


async def snapshot(mcp, network: str, pool: str, interval: str) -> dict:
    """The latest indicator values and candle for one pool, in one small dict."""
    why = f"Signal agent reading {interval} indicators for a watchlist pool."
    detail = await call(mcp, "getPoolDetails", {"network": network, "pool_address": pool, "rationale": why})
    out = {
        "pair": "/".join(clean(t.get("symbol") or "?").strip() for t in detail.get("tokens", [])),
        "network": network,
        "interval": interval,
    }
    for ind in ("rsi", "macd", "bbands"):
        series = await call(mcp, "getPoolIndicator", {
            "network": network, "pool_address": pool, "indicator": ind,
            "interval": interval, "limit": 2, "rationale": why,
        })
        latest = series["data"][-1]
        out[ind] = {k: round(v, 6) for k, v in latest.items() if k != "timestamp"}
    candles = await call(mcp, "getPoolOHLCV", {
        "network": network, "pool_address": pool, "start": "-24h", "interval": interval, "rationale": why,
    })
    out["close"] = round(candles[-1]["close"], 6)
    return out


def rule(s: dict) -> dict:
    """The fallback when no model is configured: oversold and turning up, or overbought and turning down."""
    rsi, hist = s["rsi"]["value"], s["macd"]["histogram"]
    if rsi < 30 and hist > 0:
        return {"signal": "buy", "reason": f"RSI {rsi:.0f} is oversold and the MACD histogram is positive."}
    if rsi > 70 and hist < 0:
        return {"signal": "sell", "reason": f"RSI {rsi:.0f} is overbought and the MACD histogram is negative."}
    return {"signal": "hold", "reason": f"RSI {rsi:.0f} with MACD histogram {hist:+.4g} gives no clear signal."}


async def decide(s: dict) -> dict:
    if not os.getenv("LLM_MODEL"):
        return rule(s)
    llm, name = model()
    reply = await llm.chat.completions.create(model=name, messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(s)},
    ])
    text = reply.choices[0].message.content or ""
    try:  # models sometimes wrap JSON in prose or a code fence
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return {"signal": "hold", "reason": f"Model reply was not JSON: {text[:120]}"}


def parse_pools(text: str) -> list[tuple[str, str]]:
    """'network:address,network:address' into pairs, with a clear message for a typo."""
    pools = []
    for item in (p.strip() for p in text.split(",") if p.strip()):
        network, sep, address = item.partition(":")
        if not sep or not network or not address:
            raise SystemExit(f"SIGNAL_POOLS entries look like network:pool_address, got {item!r}")
        pools.append((network.strip().lower(), address.strip()))
    return pools


async def main() -> None:
    args(__doc__)
    load_dotenv()
    interval = os.getenv("SIGNAL_INTERVAL", "1h")
    pools = parse_pools(os.getenv("SIGNAL_POOLS", DEFAULT_POOLS))
    async with dexpaprika() as mcp:
        for network, pool in pools:
            s = await snapshot(mcp, network, pool, interval)
            d = await decide(s)
            print(f"{s['pair']} on {network}: {d['signal'].upper()}  {d['reason']}")
            print(f"  close {s['close']}  rsi {s['rsi']['value']}  macd hist {s['macd']['histogram']}  "
                  f"bbands {s['bbands']['lower']} / {s['bbands']['upper']}")


if __name__ == "__main__":
    asyncio.run(main())
