# DexPaprika agent kit

[![daily](https://github.com/coinpaprika/dexpaprika-agent-kit/actions/workflows/daily.yml/badge.svg)](https://github.com/coinpaprika/dexpaprika-agent-kit/actions/workflows/daily.yml)

Three small Python agents for on-chain DEX data, built on the hosted
[DexPaprika MCP server](https://docs.dexpaprika.com/ai-integration/hosted-mcp-server).
Nothing to install on the server side, no DexPaprika key, and any chat model with tool
calling: Nebius Token Factory, OpenRouter, OpenAI or a local Ollama.

| Agent | What it does | Needs a model |
|---|---|---|
| [`market_chat.py`](market_chat.py) | Answers questions like "top WETH pools on Ethereum" or "where does PEPE trade" by picking DexPaprika tools itself | yes |
| [`pool_radar.py`](pool_radar.py) | Posts new liquidity pools on a network to Discord, Telegram or your terminal | no |
| [`signal_agent.py`](signal_agent.py) | Reads RSI, MACD and Bollinger Bands for a watchlist and returns buy, sell or hold on paper | optional |

Each one is a single file of under 120 lines. The shared plumbing is in
[`agentkit.py`](agentkit.py).

## Run it

```bash
git clone https://github.com/coinpaprika/dexpaprika-agent-kit
cd dexpaprika-agent-kit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python signal_agent.py                 # works right away, no keys
RADAR_NETWORK=solana RADAR_MIN_LIQUIDITY=1000 python pool_radar.py
```

`signal_agent.py` prints something like this:

```
USDC/WETH on ethereum: HOLD  RSI 75 with MACD histogram +5.624 gives no clear signal.
  close 2435.782789  rsi 75.117398  macd hist 5.623632  bbands 2336.905021 / 2462.623493
```

For `market_chat.py`, and for model-written signals, add a model:

```bash
cp .env.example .env    # then fill in one block: Nebius, OpenRouter, OpenAI or Ollama
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

## Daily check

[`smoke.py`](smoke.py) runs every agent's DexPaprika calls against the live server without a
model, and the [daily workflow](.github/workflows/daily.yml) runs it every morning. If the
badge at the top is green, the code here works today.

## Also

- [dexpaprika-mcp](https://github.com/coinpaprika/dexpaprika-mcp): the MCP server, to run
  locally with `npx dexpaprika-mcp@latest` or add to Claude, Cursor and other MCP clients
- [DexPaprika docs](https://docs.dexpaprika.com): the REST API behind it

The signals are an example of wiring indicators into an agent. They are not trading advice,
and nothing here places orders.

MIT licensed.
