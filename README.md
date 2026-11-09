# DexPaprika agent kit

[![daily](https://github.com/donbagger/dexpaprika-agent-kit/actions/workflows/daily.yml/badge.svg)](https://github.com/donbagger/dexpaprika-agent-kit/actions/workflows/daily.yml)
[![Powered by DexPaprika](https://dexpaprika.com/badges/powered-by-dexpaprika.svg?t=bwt_qCURn2Q05rSVNkdE)](https://dexpaprika.com)

Three small Python agents for on-chain DEX data, built on the hosted
[DexPaprika MCP server](https://docs.dexpaprika.com/ai-integration/hosted-mcp-server).
They are standalone scripts, not a package: clone, install the requirements, run a file.
You need no DexPaprika key to start. A chat model is needed only for `market_chat.py`
(optional for `signal_agent.py`), and any model with tool calling works: Nebius Token
Factory, OpenRouter, OpenAI or a local Ollama.

| Agent | What it does | Needs a model |
|---|---|---|
| [`market_chat.py`](market_chat.py) | Answers questions like "top WETH pools on Ethereum" or "where does PEPE trade" by picking DexPaprika tools itself | yes |
| [`pool_radar.py`](pool_radar.py) | Posts new liquidity pools on a network to Discord, Telegram or your terminal | no |
| [`signal_agent.py`](signal_agent.py) | Reads RSI, MACD and Bollinger Bands for a watchlist and returns buy, sell or hold on paper | optional |

Each one is a single file of under 160 lines. The shared plumbing is in
[`agentkit.py`](agentkit.py).

## Run it

```bash
git clone https://github.com/donbagger/dexpaprika-agent-kit
cd dexpaprika-agent-kit
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements.txt

python signal_agent.py                 # works right away, no keys
RADAR_NETWORK=solana RADAR_MIN_LIQUIDITY=1000 python pool_radar.py
```

`signal_agent.py` makes 5 tool calls per pool, 10 for its two default pools.
`pool_radar.py` makes 1 per pass, plus 1 for each pool it posts.

`signal_agent.py` prints something like this:

```
USDC/WETH on ethereum: HOLD  RSI 23 with MACD histogram -0.5149 gives no clear signal.
  close 2104.404538  rsi 23.213031  macd hist -0.514865  bbands 2059.961458 / 2268.470624
SOL/USDC on solana: HOLD  RSI 35 with MACD histogram -0.04008 gives no clear signal.
  close 86.236724  rsi 34.630045  macd hist -0.040077  bbands 84.989814 / 90.43505
```

For `market_chat.py`, and for model-written signals, add a model:

```bash
cp .env.example .env    # then uncomment one block and fill it in: Nebius, OpenRouter, OpenAI or Ollama
python market_chat.py "What are the top 3 WETH pools on Ethereum by 24h volume?"
python market_chat.py   # or chat interactively
```

Python 3.10 or newer.

## How it works

The agents talk to `https://mcp.dexpaprika.com/streamable-http` with the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). `agentkit.chat()` hands
the server's tool list to the model, runs whatever tools it asks for, feeds the results back
and returns the model's answer. That loop is the whole agent; the files differ in which tools
they expose and what they do with the answer.

`pool_radar.py` and `signal_agent.py` also call tools directly through `agentkit.call()`, which
is the simplest way to use DexPaprika from a script when you do not need a model at all.

## Make it yours

Click **Use this template** on GitHub, or fork. Things people usually change first:

- `TOOLS` in `market_chat.py` decides which DexPaprika tools the model sees. There are more on
  the server, for example `getNetworkPoolsFilter` for volume and liquidity filters or
  `getPoolTransactions` for individual swaps.
- `rule()` in `signal_agent.py` is the no-model strategy. `SYSTEM` is the one the model follows.
- `post()` in `pool_radar.py` is where alerts go. Slack, email or a database are a few lines each.

## Limits and keys

The hosted server answers without a key. Some tools (token-level OHLCV, token indicators,
pool swaps) need a Dev, Pro or Enterprise plan: set `DEXPAPRIKA_API_KEY` in `.env` and the kit
sends it as the `Authorization` header, the key on its own. Without it those tools return a
message that names a fallback tool, and `market_chat.py` tells the model to use it.

Calling the REST API from your own code as well? It also works without a key, and a free key
from [console.dexpaprika.com](https://console.dexpaprika.com) raises its limits. Current plans and quotas are on the [pricing page](https://dexpaprika.com/api/pricing), and
everything else is in the [docs](https://docs.dexpaprika.com).

On Ollama, start the server with `OLLAMA_CONTEXT_LENGTH=16384` or more. The tool descriptions
do not fit in the default 4k context, and a model that cannot see them picks the wrong tools.

## Tests

```bash
python -m unittest -v   # offline: fake MCP session and model, recorded tool results
python smoke.py         # live: every agent's DexPaprika calls, no model
```

The [daily workflow](.github/workflows/daily.yml) runs the offline tests on Python 3.10 to
3.13 on every push, and [`smoke.py`](smoke.py) against the live server on every push to
`main` and every morning. If the
badge at the top is green, the code here works today.

## Safety

- The model can only call the tools in `TOOLS`. Tool results are data from the internet,
  token names included, so an instruction hidden in one cannot reach any other tool.
- `DEXPAPRIKA_API_KEY` is only ever sent to `https://mcp.dexpaprika.com`. Point
  `DEXPAPRIKA_MCP_URL` anywhere else and the kit refuses to start with a key set.
- Alerts from `pool_radar.py` go out as plain text with mentions turned off, so a token
  called `@everyone` cannot ping your Discord.
- The Telegram bot token is part of Telegram's API URL. pool-radar never prints or logs
  that URL, even when a post fails, and no webhook post follows a redirect.
- Every connection has explicit timeouts and refuses redirects.
- Dependencies are pinned with hashes and CI actions to commit SHAs. More in
  [SECURITY.md](SECURITY.md).

Coding agents working on this repo should read [AGENTS.md](AGENTS.md) first.

## Also

- [dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp): the MCP server, to run
  locally with `npx dexpaprika-mcp@latest` or add to Claude, Cursor and other MCP clients
- [DexPaprika docs](https://docs.dexpaprika.com): the REST API behind it
- [pool-pulse](https://github.com/donbagger/pool-pulse): the most traded pools on a network,
  a Node CLI on the REST API
- [pool-depth](https://github.com/donbagger/pool-depth): is a pool deep enough for your trade,
  in Python on the official SDK

The signals are an example of wiring indicators into an agent. They are not trading advice,
and nothing here places orders.

MIT licensed.
