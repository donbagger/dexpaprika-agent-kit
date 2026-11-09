"""pool-radar: post new liquidity pools to Discord, Telegram or the terminal.

Every few minutes it asks DexPaprika for the newest pools on one network, skips the ones
below a liquidity floor, and sends the rest with token symbols and a link. No model and no
DexPaprika key are needed.

    python pool_radar.py                      # watch, with settings from .env
    python pool_radar.py --once               # one pass, then exit (for cron)
    RADAR_NETWORK=solana python pool_radar.py

Settings (environment or .env):
    RADAR_NETWORK           network id, default base
    RADAR_MIN_LIQUIDITY     USD floor, default 5000
    RADAR_INTERVAL          seconds between passes, default 300, at least 60
    DISCORD_WEBHOOK_URL     post to a Discord channel
    TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID   post to a Telegram chat
"""

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from agentkit import args, call, clean, dexpaprika, label, load_dotenv

STATE_FILE = ".pool_radar_seen.json"


DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


def post(text: str) -> None:
    """Send one message wherever the environment says. Prints when nothing is configured."""
    discord = os.getenv("DISCORD_WEBHOOK_URL")
    tg_token, tg_chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if discord:
        # No mentions: a token named "@everyone" must not ping the whole server.
        send("Discord", discord, {"content": text, "allowed_mentions": {"parse": []}})
    if tg_token and tg_chat:
        # Plain text (no parse_mode), so token names cannot inject markup or links.
        send("Telegram", f"https://api.telegram.org/bot{quote(tg_token, safe=':')}/sendMessage",
             {"chat_id": tg_chat, "text": text, "disable_web_page_preview": True})
    if not discord and not (tg_token and tg_chat):
        print(text + "\n")


def check_settings() -> None:
    """Fail at start-up, not on the first alert, if a webhook is not what it says it is."""
    discord = os.getenv("DISCORD_WEBHOOK_URL")
    if discord:
        parts = urlsplit(discord)
        if parts.scheme != "https" or parts.hostname not in DISCORD_HOSTS or not parts.path.startswith("/api/webhooks/"):
            raise SystemExit("DISCORD_WEBHOOK_URL should look like https://discord.com/api/webhooks/...")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect becomes an HTTPError, not a second request
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def send(where: str, url: str, body: dict) -> None:
    """POST one alert. A failed post is reported and skipped, so the radar keeps running.

    The error names the service and the HTTP status only: the Telegram URL carries the bot
    token, so neither the URL nor the exception text is printed.
    """
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "pool-radar"})
    try:
        _opener.open(req, timeout=15).close()
    except urllib.error.HTTPError as e:
        print(f"{where} refused the alert: HTTP {e.code}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, OSError):
        print(f"Could not reach {where}; the alert was skipped.", file=sys.stderr)


def load_seen() -> dict[str, None]:
    """Pool ids already handled, oldest first. A dict keeps insertion order, a set does not."""
    try:
        with open(STATE_FILE) as f:
            return dict.fromkeys(json.load(f))
    except (OSError, ValueError):
        return {}


def save_seen(seen: dict[str, None]) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen)[-2000:], f)


async def scan(mcp, network: str, floor: float, seen: dict[str, None]) -> int:
    """One pass: the 50 newest pools, filtered and posted. Returns how many were posted.

    Cost: one getNetworkPools call, plus one getPoolDetails call per pool that gets posted.
    """
    newest = await call(mcp, "getNetworkPools", {
        "network": network, "sort_by": "created_at", "sort_dir": "desc", "limit": 50,
        "rationale": f"Pool radar checking for new pools on {network} to alert a channel.",
    })
    first_run = not seen
    posted = 0
    for pool in newest["results"]:
        if pool["id"] in seen:
            continue
        seen[pool["id"]] = None
        if first_run or (pool.get("liquidity_usd") or 0) < floor:
            continue  # the first pass only learns what already exists
        detail = await call(mcp, "getPoolDetails", {
            "network": network, "pool_address": pool["id"],
            "rationale": "Pool radar fetching token symbols for a new pool before alerting.",
        })
        pair = "/".join(label(t.get("symbol") or t["id"][:8], 24) for t in detail.get("tokens", []))
        post(
            f"New pool on {network}: {pair} ({label(pool['dex_name'])})\n"
            f"Liquidity ${pool.get('liquidity_usd') or 0:,.0f} · 24h volume ${pool.get('volume_usd_24h') or 0:,.0f} "
            f"· {pool.get('transactions_24h') or 0} txns · created {clean(pool['created_at'])}\n"
            f"https://dexpaprika.com/{network}/pool/{quote(pool['id'], safe='')}"
        )
        posted += 1
    return posted


async def main() -> None:
    once = "--once" in args(__doc__, flags=("--once",))
    load_dotenv()
    network = os.getenv("RADAR_NETWORK", "base").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", network):
        raise SystemExit(f"RADAR_NETWORK should be a network id such as base or solana, got {network!r}")
    floor = float(os.getenv("RADAR_MIN_LIQUIDITY", "5000"))
    interval = max(60, int(os.getenv("RADAR_INTERVAL", "300")))
    check_settings()
    seen = load_seen()
    print(f"Watching {network} for new pools with at least ${floor:,.0f} liquidity.")
    while True:
        try:
            async with dexpaprika() as mcp:
                posted = await scan(mcp, network, floor, seen)
        except Exception as e:  # a watcher outlives a bad minute: report it and try next pass
            if once:
                raise
            print(f"Pass failed ({e.__class__.__name__}: {str(e)[:200]}), retrying in {interval}s.", file=sys.stderr)
        else:
            save_seen(seen)
            print(f"Pass done, {posted} posted, {len(seen)} pools known.")
        if once:
            return
        await asyncio.sleep(interval)


if __name__ == "__main__":
    asyncio.run(main())
