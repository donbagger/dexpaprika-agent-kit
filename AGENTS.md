# AGENTS.md

Notes for coding agents (Claude Code, Codex, Cursor and the like) working in this repo.

## Setup and checks

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.txt
python -m unittest -v    # offline, under a second, run it after every change
python smoke.py          # live MCP server, no model, run it before you push
```

`python signal_agent.py` runs with no keys at all. `market_chat.py` needs a model:
`LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` (see `.env.example`).

## Layout

- `agentkit.py`: the MCP session (`dexpaprika()`), direct tool calls (`call()`), the
  model loop (`chat()`), `.env` loading and `clean()`. Every agent imports from here.
- `market_chat.py`, `pool_radar.py`, `signal_agent.py`: one agent each, kept short enough
  to read in one sitting. Add a new agent as a new file in the same shape.
- `tests/test_kit.py`: fake MCP session and fake model; fixtures in `tests/fixtures/` are
  unedited tool results from the live server. Re-record them with
  `python scripts/record_fixtures.py` (7 tool calls), never hand-write fields.

## DexPaprika MCP facts that trip agents up

- Server: `https://mcp.dexpaprika.com/streamable-http`. Every read tool needs a
  `rationale` string of 20 to 500 characters saying why it is called.
- `getNetworks` lists network ids; `search` finds a token or pool by name before you need
  its address.
- Token-level OHLCV, token indicators and pool swaps need a Dev, Pro or Enterprise key in
  `DEXPAPRIKA_API_KEY`; without it they return an error that names a fallback tool.
  Plans: https://dexpaprika.com/api/pricing. Keys: https://console.dexpaprika.com.
  Docs: https://docs.dexpaprika.com.
- Read tool result fields off a live call (`smoke.py` or `call()` in a REPL). Do not guess.
- `call()` fills in a default `rationale` when a script leaves it out. Tool calls the model
  makes inside `chat()` go to the server as the model wrote them, so the system prompt has
  to ask for a rationale (as `market_chat.py` does); without one the server refuses the call
  and the model sees that error.
- Time budget: MCP connections time out after 30 s, reads after 300 s (the server can hold
  a stream open), a model reply after 120 s, and `chat()` stops after 8 rounds of tool
  calls. Give your own loop a deadline.
- Tool calls per run: `signal_agent.py` makes 5 per pool in `SIGNAL_POOLS` (1
  `getPoolDetails`, 3 `getPoolIndicator`, 1 `getPoolOHLCV`), so 10 with the two default
  pools. `pool_radar.py` makes 1 `getNetworkPools` per pass plus 1 `getPoolDetails` per
  pool it posts, one pass every `RADAR_INTERVAL` seconds. `market_chat.py` makes whatever
  the model asks for, at most 8 rounds per question. `smoke.py` makes 11 and lists the
  tools once. When iterating, set `SIGNAL_POOLS` to one pool and run
  `pool_radar.py --once`.

## Rules that keep it safe

- `chat()` only runs tools that were offered to the model. Keep that check; tool results
  are untrusted text.
- The key is sent as the `Authorization` header, the key on its own, and only to
  `mcp.dexpaprika.com` (`check_mcp_url`). Never log or print it.
- Strings from tool results go through `clean()` before a model, a terminal or a chat sees
  them, and token names in alerts go through `label()`. Discord posts keep
  `allowed_mentions` empty.
- Check settings (`LLM_MODEL`, webhook URLs, network ids) before `dexpaprika()` opens a
  connection. A `SystemExit` raised inside it surfaces as an exception group traceback.
- Each script starts with `args(__doc__, ...)`, which prints the docstring for `--help` and
  refuses unknown flags. `load_dotenv()` stops when `.env` sets a key twice, so two model
  blocks left uncommented cannot silently pick the wrong provider.
- `httpx2` is the HTTP client the MCP SDK is built on, the successor to `httpx`. It is not a
  typo; do not swap it for `httpx`.
- Dependencies: edit `requirements.in`, then regenerate `requirements.txt` with the command
  at its top. Do not add packages for things the standard library does.
- No trading: the signals are paper only. Do not add order placement.
- Do not hard-code rate limits or quotas; link the pricing page.
