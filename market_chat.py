"""market-chat: ask about DEX markets in plain language.

The model picks DexPaprika tools on its own: pools, tokens, prices, OHLCV, indicators and
search across every chain DexPaprika indexes. No DexPaprika key is needed.

    python market_chat.py                                  # interactive
    python market_chat.py "top 5 WETH pools on Ethereum"   # one question, then exit
"""

import asyncio
import sys
from datetime import date

from agentkit import chat, dexpaprika, load_dotenv, openai_tools

# The tools a market question needs. The server has more (filters, per-DEX lists, swaps);
# a shorter list keeps the prompt small enough for local models. Add names here to widen it.
TOOLS = [
    "search", "getNetworks", "getNetworkPools", "getTopTokens", "getTokenDetails",
    "getTokenPools", "getTokenMultiPrices", "getPoolDetails", "getPoolOHLCV", "getPoolIndicator",
]

SYSTEM = f"""You answer questions about decentralized exchange markets using the DexPaprika tools.
Today is {date.today().isoformat()}.

- Take every number from a tool result. Never estimate a price, volume or liquidity figure.
- Chain names are network ids such as ethereum, solana, base, bsc. Call getNetworks if unsure.
- To find a token or pool by name or symbol, call search first, then use the address it returns.
- Every read tool needs a short `rationale`: one sentence on why you are calling it.
- If a tool says it needs an API key, say so and use the fallback tool it suggests.
- Answer in a few lines. Name the network and show addresses for pools and tokens you mention.
"""


async def main() -> None:
    load_dotenv()
    question = " ".join(sys.argv[1:]).strip()
    async with dexpaprika() as mcp:
        tools = await openai_tools(mcp, only=TOOLS)
        messages = [{"role": "system", "content": SYSTEM}]
        while True:
            if not question:
                try:
                    question = input("\nyou> ").strip()
                except (EOFError, KeyboardInterrupt):
                    return
                if question.lower() in {"", "exit", "quit"}:
                    return
            messages.append({"role": "user", "content": question})
            print("\n" + await chat(mcp, messages, tools))
            if len(sys.argv) > 1:
                return
            question = ""


if __name__ == "__main__":
    asyncio.run(main())
