# Security

Please report a vulnerability privately through
[GitHub's private reporting](https://github.com/donbagger/dexpaprika-agent-kit/security/advisories/new)
rather than in a public issue. I aim to answer within a few days.

## Keys

- `DEXPAPRIKA_API_KEY` is optional. When set, it is sent in the `Authorization` header,
  the key on its own, and only to `https://mcp.dexpaprika.com`. A different
  `DEXPAPRIKA_MCP_URL` with a key set stops the kit before it connects.
- `LLM_API_KEY` goes only to `LLM_BASE_URL`, through the official `openai` client.
- `TELEGRAM_BOT_TOKEN` is part of Telegram's API URL by design. pool-radar never prints
  that URL or the exception text that could contain it.
- Keys live in `.env`, which is in `.gitignore`. Nothing is written to disk except the list
  of pool ids pool-radar has already seen.

## Untrusted data

Token names and symbols in tool results are set by whoever deployed the token.

- The model in `market_chat.py` can only call the tools listed in `TOOLS`. A tool result
  that tells it to do something else cannot reach any other tool, and every DexPaprika
  tool on that list only reads public market data.
- Text from tool results is stripped of control characters and bidirectional overrides
  before it reaches a model, a terminal or a chat.
- In alerts, token symbols and DEX names also lose markup characters and URL schemes, so a
  token named like a link cannot post as one. Discord alerts are sent with mentions
  disabled, Telegram alerts as plain text.

## Supply chain

Dependencies are installed from a requirements file pinned with hashes. CI actions are
pinned to commit SHAs, Dependabot keeps both current, and the workflow token is read-only.

The signals are paper only. Nothing in this repo places orders or holds funds.
