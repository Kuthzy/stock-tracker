"""Telegram commands. Each command is a small function registered as a plug-in, so new
commands are easy to add (see plugins/example_news_command.py).
Only messages from TELEGRAM_CHAT_ID are obeyed."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

import requests

from .. import plugins
from ..models import utcnow
from ..plugins import register
from ..storage import Database

log = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-=^]{0,9}$")


@dataclass
class CommandContext:
    """What a command function receives."""

    args: list[str]                       # words after the command, e.g. ["BWET"]
    db: Database
    universe: Callable[[], list[str]]     # current watchlist
    engine: object = None                 # the running Engine, when available

    @property
    def ticker(self) -> str:
        return self.args[0].upper() if self.args else ""


@register("commands", "/help")
def cmd_help(ctx: CommandContext) -> str:
    """/help - list commands"""
    lines = ["Commands:"]
    for name in plugins.available("commands"):
        fn = plugins.get("commands", name)
        doc = (fn.__doc__ or name).strip().splitlines()[0]
        lines.append(doc)
    return "\n".join(lines)


register("commands", "/start")(cmd_help)


@register("commands", "/list")
def cmd_list(ctx: CommandContext) -> str:
    """/list - show watchlist"""
    return "Watching: " + ", ".join(ctx.universe())


@register("commands", "/watch")
def cmd_watch(ctx: CommandContext) -> str:
    """/watch TICKER - add to watchlist"""
    if not TICKER_RE.match(ctx.ticker):
        return "Usage: /watch TICKER"
    ctx.db.set_override(ctx.ticker, "add")
    return f"Added {ctx.ticker}."


@register("commands", "/unwatch")
def cmd_unwatch(ctx: CommandContext) -> str:
    """/unwatch TICKER - remove from watchlist"""
    if not TICKER_RE.match(ctx.ticker):
        return "Usage: /unwatch TICKER"
    ctx.db.set_override(ctx.ticker, "remove")
    return f"Removed {ctx.ticker}."


@register("commands", "/score")
def cmd_score(ctx: CommandContext) -> str:
    """/score TICKER - latest Move Score and why"""
    row = ctx.db.latest_score(ctx.ticker) if ctx.ticker else None
    if not row:
        return f"No score yet for {ctx.ticker or '?'}."
    comps = json.loads(row["components"])
    reasons = json.loads(row["reasons"])
    return (f"{ctx.ticker}: {row['score']:.0f}/100 ({row['direction']})\n"
            + " · ".join(f"{k} {v:.0f}" for k, v in comps.items())
            + ("\nWhy: " + "; ".join(reasons) if reasons else ""))


@register("commands", "/top")
def cmd_top(ctx: CommandContext) -> str:
    """/top - top 10 scores now"""
    rows = ctx.db.latest_scores()[:10]
    return "\n".join(f"{r['ticker']:<6} {r['score']:>5.0f}  {r['direction']}" for r in rows) or "No scores yet."


@register("commands", "/mute")
def cmd_mute(ctx: CommandContext) -> str:
    """/mute 1h - pause non-urgent alerts (30m, 2h...)"""
    spec = (ctx.args[0] if ctx.args else "1h").lower()
    m = re.match(r"^(\d+)([mh])$", spec)
    if not m:
        return "Usage: /mute 30m or /mute 2h"
    delta = timedelta(minutes=int(m[1])) if m[2] == "m" else timedelta(hours=int(m[1]))
    ctx.db.kv_set("mute_until", (utcnow() + delta).isoformat())
    return f"Muted non-urgent alerts for {spec}."


@register("commands", "/unmute")
def cmd_unmute(ctx: CommandContext) -> str:
    """/unmute - resume alerts"""
    ctx.db.kv_set("mute_until", "")
    return "Alerts resumed."


class TelegramCommands:
    def __init__(self, token: str, chat_id: str, db: Database, universe_fn, engine=None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.db = db
        self.universe_fn = universe_fn
        self.engine = engine
        self._backoff = False

    def _send(self, text: str) -> None:
        try:
            requests.post(f"{self.base}/sendMessage", json={"chat_id": self.chat_id, "text": text[:4000]},
                          timeout=15)
        except requests.RequestException as exc:
            log.warning("telegram reply failed: %s", type(exc).__name__)

    def handle(self, text: str) -> str:
        parts = text.strip().split()
        if not parts:
            return cmd_help(CommandContext([], self.db, self.universe_fn, self.engine))
        cmd = parts[0].split("@")[0].lower()
        ctx = CommandContext(parts[1:], self.db, self.universe_fn, self.engine)
        fn = plugins.get("commands", cmd) or cmd_help
        try:
            return str(fn(ctx))
        except Exception:
            log.exception("command %s failed", cmd)
            return f"Sorry, {cmd} failed. Check the logs."

    def _poll_once(self, offset: int, wait: int = 25) -> int:
        try:
            resp = requests.get(f"{self.base}/getUpdates", params={"offset": offset, "timeout": wait},
                                timeout=wait + 10)
            data = resp.json()
        except (requests.RequestException, ValueError):
            return offset
        if not data.get("ok"):
            log.warning("telegram commands: %s (check TELEGRAM_BOT_TOKEN)", data.get("description", "error"))
            self._backoff = True
            return offset
        updates = data.get("result", [])
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue  # ignore strangers
            if (msg.get("text") or "").startswith("/"):
                self._send(self.handle(msg["text"]))
        return offset

    def poll_pending(self) -> None:
        """Answer any commands sent since the last check (used by scheduled cloud runs)."""
        offset = int(self.db.kv_get("tg_offset", "0") or 0)
        self.db.kv_set("tg_offset", str(self._poll_once(offset, wait=0)))

    async def run(self) -> None:
        offset = int(self.db.kv_get("tg_offset", "0") or 0)
        while True:
            self._backoff = False
            offset = await asyncio.to_thread(self._poll_once, offset)
            self.db.kv_set("tg_offset", str(offset))
            await asyncio.sleep(60 if self._backoff else 1)
