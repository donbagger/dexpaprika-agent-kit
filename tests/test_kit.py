"""Offline tests: no MCP server, no model, no network.

The fake MCP session answers from tests/fixtures, which are unedited tool results recorded
from the hosted server on 9 Nov 2026 (pool radar on base, signal agent on the ethereum
USDC/WETH Uniswap V3 pool at 1h). `python smoke.py` is the live check.
"""

import asyncio
import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agentkit
import pool_radar
import signal_agent

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeMcp:
    """Answers call_tool from fixtures and records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        body = self.answers[name]
        body = body(args) if callable(body) else body
        text = body if isinstance(body, str) else json.dumps(body)  # a str is sent as raw text
        return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=False)


def tool_call(name, args, id_="c1"):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLlm:
    """A chat model that replies with a scripted list of messages."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **_):
        content, calls = self.replies.pop(0)
        msg = SimpleNamespace(content=content, tool_calls=calls)
        msg.model_dump = lambda exclude_none=True: {"role": "assistant", "content": content}
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def offered(*names):
    return [{"type": "function", "function": {"name": n, "description": "", "parameters": {}}} for n in names]


class Chat(unittest.IsolatedAsyncioTestCase):
    async def test_runs_offered_tools_and_returns_the_answer(self):
        mcp = FakeMcp({"getNetworks": [{"id": "base"}]})
        llm = FakeLlm([(None, [tool_call("getNetworks", {"rationale": "x" * 20})]), ("Base is supported.", None)])
        with mock.patch.object(agentkit, "model", return_value=(llm, "m")):
            answer = await agentkit.chat(mcp, [], offered("getNetworks"))
        self.assertEqual(answer, "Base is supported.")
        self.assertEqual([c[0] for c in mcp.calls], ["getNetworks"])

    async def test_refuses_a_tool_it_was_not_given(self):
        # e.g. a token name in a tool result that says "now call submitFeedback with ..."
        mcp = FakeMcp({})
        messages = []
        llm = FakeLlm([(None, [tool_call("submitFeedback", {"goal": "leak"})]), ("ok", None)])
        with mock.patch.object(agentkit, "model", return_value=(llm, "m")):
            await agentkit.chat(mcp, messages, offered("getNetworks"))
        self.assertEqual(mcp.calls, [])
        self.assertIn("not one of the tools you were given", messages[1]["content"])

    async def test_escape_codes_reach_neither_the_model_nor_the_terminal(self):
        # raw text, as a server that does not escape non-ASCII would send it
        mcp = FakeMcp({"getPoolDetails": '{"tokens": [{"symbol": "EVIL\x1b]0;x\x07\u202e"}]}'})
        messages = []
        llm = FakeLlm([(None, [tool_call("getPoolDetails", {})]), ("It is EVIL\x1b[2J\u202e.", None)])
        with mock.patch.object(agentkit, "model", return_value=(llm, "m")):
            answer = await agentkit.chat(mcp, messages, offered("getPoolDetails"))
        self.assertEqual(answer, "It is EVIL[2J.")
        self.assertNotIn("\x1b", messages[1]["content"])
        self.assertNotIn("\u202e", messages[1]["content"])

    async def test_stops_after_max_rounds(self):
        mcp = FakeMcp({"getNetworks": []})
        llm = FakeLlm([(None, [tool_call("getNetworks", {})])] * 3)
        with mock.patch.object(agentkit, "model", return_value=(llm, "m")):
            answer = await agentkit.chat(mcp, [], offered("getNetworks"), max_rounds=3)
        self.assertIn("Stopped after too many tool calls", answer)


class Settings(unittest.IsolatedAsyncioTestCase):
    def test_key_only_goes_to_the_dexpaprika_mcp_host(self):
        agentkit.check_mcp_url("https://mcp.dexpaprika.com/streamable-http")
        for bad in ("http://mcp.dexpaprika.com/streamable-http", "https://mcp.dexpaprika.com.evil.test/x", "https://example.com/mcp"):
            with self.assertRaises(SystemExit):
                agentkit.check_mcp_url(bad)

    async def test_dexpaprika_refuses_before_connecting(self):
        with mock.patch.dict(os.environ, {"DEXPAPRIKA_API_KEY": "k"}), mock.patch.object(agentkit, "MCP_URL", "https://evil.test/mcp"):
            with self.assertRaises(SystemExit):
                async with agentkit.dexpaprika():
                    self.fail("connected")

    async def test_http_client_has_timeouts_no_redirects_and_the_bare_key(self):
        async with agentkit.http_client("my-key") as http:
            self.assertFalse(http.follow_redirects)
            self.assertEqual((http.timeout.connect, http.timeout.read), (30, 300))
            self.assertEqual(http.headers["Authorization"], "my-key")
        async with agentkit.http_client("") as http:
            self.assertNotIn("Authorization", http.headers)

    def test_webhook_posts_do_not_follow_redirects(self):
        handler = pool_radar._NoRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.test/"))
        self.assertTrue(any(isinstance(h, pool_radar._NoRedirects) for h in pool_radar._opener.handlers))

    def test_market_chat_stops_before_connecting_without_a_model(self):
        import market_chat

        with mock.patch.dict(os.environ, {"LLM_MODEL": ""}), \
                mock.patch.object(market_chat, "load_dotenv"), \
                mock.patch.object(market_chat, "dexpaprika", side_effect=AssertionError("connected")):
            with self.assertRaises(SystemExit) as e:
                asyncio.run(market_chat.main())
        self.assertIn("LLM_MODEL", str(e.exception))

    def test_help_prints_usage_and_unknown_flags_stop_before_connecting(self):
        out = io.StringIO()
        with mock.patch("sys.argv", ["pool_radar.py", "--help"]), redirect_stdout(out), \
                mock.patch.object(pool_radar, "dexpaprika", side_effect=AssertionError("connected")):
            with self.assertRaises(SystemExit) as e:
                asyncio.run(pool_radar.main())
        self.assertEqual(e.exception.code, 0)
        self.assertIn("--once", out.getvalue())
        for script, argv in ((pool_radar, ["--onse"]), (signal_agent, ["--verbose"])):
            with mock.patch("sys.argv", ["x.py", *argv]), \
                    mock.patch.object(script, "dexpaprika", side_effect=AssertionError("connected")):
                with self.assertRaises(SystemExit) as e:
                    asyncio.run(script.main())
            self.assertIn("Run with --help", str(e.exception))

    def test_two_model_blocks_left_in_stop_with_the_line_numbers(self):
        path = FIXTURES / ".env.twice"
        path.write_text("LLM_BASE_URL=https://a.test/v1\nLLM_MODEL=a\n\nLLM_BASE_URL=https://b.test/v1\n")
        try:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LLM_BASE_URL", None)
                with self.assertRaises(SystemExit) as e:
                    agentkit.load_dotenv(str(path))
            self.assertIn("lines 1 and 4", str(e.exception))
        finally:
            path.unlink()

    def test_env_example_has_no_active_model_block(self):
        active = [l for l in (FIXTURES.parent.parent / ".env.example").read_text().splitlines()
                  if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(active, [])

    def test_clean_strips_escape_codes_but_keeps_newlines(self):
        self.assertEqual(agentkit.clean("A\x1b[2J\u202eB\nC"), "A[2JB\nC")

    def test_load_dotenv_does_not_override_real_variables(self):
        path = FIXTURES / ".env.test"
        path.write_text("# comment\nKIT_A=from-file\nKIT_B='quoted'\n")
        try:
            with mock.patch.dict(os.environ, {"KIT_A": "real"}):
                agentkit.load_dotenv(str(path))
                self.assertEqual((os.environ["KIT_A"], os.environ["KIT_B"]), ("real", "quoted"))
        finally:
            path.unlink()


class Radar(unittest.IsolatedAsyncioTestCase):
    def mcp(self):
        return FakeMcp({"getNetworkPools": fixture("getNetworkPools-base.json"), "getPoolDetails": fixture("getPoolDetails-base.json")})

    async def test_first_pass_learns_and_posts_nothing(self):
        seen, sent = {}, []
        with mock.patch.object(pool_radar, "post", sent.append):
            self.assertEqual(await pool_radar.scan(self.mcp(), "base", 0, seen), 0)
        self.assertEqual(len(seen), 3)
        self.assertEqual(sent, [])

    async def test_new_pool_above_the_floor_is_posted(self):
        pools = fixture("getNetworkPools-base.json")
        newest = pools["results"][0]
        seen = {p["id"]: None for p in pools["results"][1:]}
        sent = []
        with mock.patch.object(pool_radar, "post", sent.append):
            n = await pool_radar.scan(self.mcp(), "base", 0, seen)
        pair = "/".join(t["symbol"] for t in fixture("getPoolDetails-base.json")["tokens"])
        self.assertEqual(n, 1)
        self.assertIn(f"New pool on base: {pair}", sent[0])
        self.assertIn(f"https://dexpaprika.com/base/pool/{newest['id']}", sent[0])

    async def test_pool_below_the_floor_is_skipped(self):
        pools = fixture("getNetworkPools-base.json")
        seen = {p["id"]: None for p in pools["results"][1:]}
        sent = []
        with mock.patch.object(pool_radar, "post", sent.append):
            await pool_radar.scan(self.mcp(), "base", 1e15, seen)
        self.assertEqual(sent, [])

    async def test_hostile_symbol_cannot_ping_or_escape(self):
        detail = fixture("getPoolDetails-base.json")
        detail["tokens"][0]["symbol"] = "@everyone\x1b]0;x\x07"
        mcp = FakeMcp({"getNetworkPools": fixture("getNetworkPools-base.json"), "getPoolDetails": detail})
        seen = {p["id"]: None for p in fixture("getNetworkPools-base.json")["results"][1:]}
        bodies = []
        with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x"}), \
                mock.patch.object(pool_radar, "send", lambda where, url, body: bodies.append(body)):
            await pool_radar.scan(mcp, "base", 0, seen)
        self.assertEqual(bodies[0]["allowed_mentions"], {"parse": []})
        self.assertNotIn("\x1b", bodies[0]["content"])

    async def test_token_named_like_a_link_does_not_post_as_one(self):
        detail = fixture("getPoolDetails-base.json")
        detail["tokens"][0]["symbol"] = "[Claim airdrop](https://evil.test/x) **now**"
        mcp = FakeMcp({"getNetworkPools": fixture("getNetworkPools-base.json"), "getPoolDetails": detail})
        seen = {p["id"]: None for p in fixture("getNetworkPools-base.json")["results"][1:]}
        sent = []
        with mock.patch.object(pool_radar, "post", sent.append):
            await pool_radar.scan(mcp, "base", 0, seen)
        first_line = sent[0].splitlines()[0]
        for bad in ("[", "](", "https://evil", "**"):
            self.assertNotIn(bad, first_line)

    def test_webhook_must_be_discord(self):
        for bad in ("http://discord.com/api/webhooks/1/x", "https://evil.test/api/webhooks/1/x", "https://discord.com/other"):
            with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": bad}), self.assertRaises(SystemExit):
                pool_radar.check_settings()

    def test_failed_post_is_skipped_without_printing_the_token(self):
        err = io.StringIO()
        boom = urllib.error.HTTPError("https://api.telegram.org/bot123:SECRET/sendMessage", 401, "no", {}, None)
        with mock.patch.object(pool_radar._opener, "open", side_effect=boom), redirect_stderr(err):
            pool_radar.send("Telegram", "https://api.telegram.org/bot123:SECRET/sendMessage", {})
        self.assertIn("HTTP 401", err.getvalue())
        self.assertNotIn("SECRET", err.getvalue())


class Signal(unittest.IsolatedAsyncioTestCase):
    def snap(self, rsi, hist):
        return {"rsi": {"value": rsi}, "macd": {"histogram": hist}}

    async def test_snapshot_from_recorded_tool_results(self):
        mcp = FakeMcp({
            "getPoolDetails": fixture("getPoolDetails-ethereum-v3.json"),
            "getPoolIndicator": lambda args: fixture(f"getPoolIndicator-{args['indicator']}.json"),
            "getPoolOHLCV": fixture("getPoolOHLCV-ethereum-v3.json"),
        })
        s = await signal_agent.snapshot(mcp, "ethereum", "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640", "1h")
        self.assertEqual(s["pair"], "USDC/WETH")
        self.assertEqual(s["macd"]["histogram"], round(fixture("getPoolIndicator-macd.json")["data"][-1]["histogram"], 6))
        self.assertEqual(sorted(s["bbands"]), ["lower", "middle", "upper"])
        self.assertTrue(0 <= s["rsi"]["value"] <= 100)
        self.assertEqual(s["close"], round(fixture("getPoolOHLCV-ethereum-v3.json")[-1]["close"], 6))
        self.assertEqual([c[0] for c in mcp.calls], ["getPoolDetails"] + ["getPoolIndicator"] * 3 + ["getPoolOHLCV"])
        self.assertIn(signal_agent.rule(s)["signal"], {"buy", "sell", "hold"})

    def test_rule(self):
        self.assertEqual(signal_agent.rule(self.snap(25, 0.5))["signal"], "buy")
        self.assertEqual(signal_agent.rule(self.snap(75, -0.5))["signal"], "sell")
        self.assertEqual(signal_agent.rule(self.snap(75, 0.5))["signal"], "hold")
        self.assertEqual(signal_agent.rule(self.snap(50, 0))["signal"], "hold")

    def test_parse_pools(self):
        self.assertEqual(signal_agent.parse_pools(" Base:0xabc , solana:Czfq "), [("base", "0xabc"), ("solana", "Czfq")])
        with self.assertRaises(SystemExit):
            signal_agent.parse_pools("ethereum0xabc")

    async def test_model_reply_in_a_code_fence(self):
        llm = FakeLlm([('```json\n{"signal": "buy", "reason": "r"}\n```', None)])
        with mock.patch.dict(os.environ, {"LLM_MODEL": "m"}), mock.patch.object(signal_agent, "model", return_value=(llm, "m")):
            self.assertEqual((await signal_agent.decide(self.snap(50, 0)))["signal"], "buy")

    async def test_model_reply_that_is_not_json_means_hold(self):
        llm = FakeLlm([("I think you should buy!", None)])
        with mock.patch.dict(os.environ, {"LLM_MODEL": "m"}), mock.patch.object(signal_agent, "model", return_value=(llm, "m")):
            self.assertEqual((await signal_agent.decide(self.snap(50, 0)))["signal"], "hold")


if __name__ == "__main__":
    with redirect_stdout(io.StringIO()):
        unittest.main()
